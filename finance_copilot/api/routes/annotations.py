"""Annotation routes: create, patch, confirm, review-queue listing, and background jobs."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Literal

import ulid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from finance_copilot.api.deps import get_db
from finance_copilot.api.live_jobs import live_job_ids, tracks_live_job
from finance_copilot.config import settings
from finance_copilot.db.queries.annotations import (
    count_snoozed,
    get_annotation,
    get_annotation_by_transaction,
    insert_annotation,
    list_review_queue,
    snooze_annotation,
    unsnooze_all,
    update_annotation,
)
from finance_copilot.db.queries.app_settings import get_confidence_threshold, get_dev_mode
from finance_copilot.db.queries.categories import (
    CategoryError,
    require_category_ids,
    resolve_category_ids,
)
from finance_copilot.db.queries.common import dump_string_list, parse_string_list
from finance_copilot.db.queries.embeddings import find_similar
from finance_copilot.db.queries.feedback_stats import category_accuracy, record_feedback
from finance_copilot.models.annotation import (
    Annotation,
    AnnotationCreate,
    AnnotationPatch,
    AutoAnnotateResult,
)
from finance_copilot.pipeline.annotate import (
    auto_annotate,
    reevaluate_after_teaching,
    rescore_review_queue,
)
from finance_copilot.pipeline.embed import (
    build_embed_text,
    embed_transaction,
    embed_transactions,
    get_embedding_single,
)
from finance_copilot.pipeline.review_queue import group_review_queue
from finance_copilot.pipeline.sources import MACHINE_SOURCES as _MACHINE_SOURCES
from finance_copilot.pipeline.sources import MODEL_SOURCES as _MODEL_SOURCES

logger = logging.getLogger(__name__)

router = APIRouter()


class AutoAnnotateRequest(BaseModel):
    statement_id: str | None = None
    transaction_ids: list[str] | None = None


@router.post("/auto-annotate", response_model=AutoAnnotateResult)
def auto_annotate_endpoint(
    body: AutoAnnotateRequest,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Synchronous annotation, for scripts and the CLI. Every UI entry point uses
    the job flow (POST /auto-annotate/jobs), which adds the single-job guard,
    progress, and the stale-job reaper; this path has none of them."""
    return auto_annotate(conn, body.statement_id, body.transaction_ids)


# Background work has no user waiting on each write, so it waits longer for
# the lock than a request does: a burst of request-path writes (a Teach me
# re-evaluation, say) must not fail a long annotation run.
_JOB_BUSY_TIMEOUT_MS = 30_000


def _open_job_connection() -> sqlite3.Connection:
    """Background tasks outlive the request's connection; open a fresh one."""
    from finance_copilot.db.connection import get_connection

    conn = get_connection()
    conn.execute(f"PRAGMA busy_timeout={_JOB_BUSY_TIMEOUT_MS}")
    return conn


def _embed_in_background(transaction_ids: list[str]) -> None:
    """Re-embed relabelled rows after the response so they serve as RAG donors,
    then let the new labels re-decide the machine labels they could change.

    A bulk edit can touch a thousand rows; embedding them inline kept the UI on
    "Applying..." for minutes. embed_transactions is best-effort (a dead
    provider leaves gaps that /embeddings/generate backfills).
    """
    conn = _open_job_connection()
    try:
        embed_transactions(conn, transaction_ids)
        reevaluate_after_teaching(conn, transaction_ids)
    finally:
        conn.close()


# A job with a live worker in this process is never reaped (live_jobs), so
# this window only has to catch orphans: a worker that died, or a task that
# never got scheduled. Workers still heartbeat updated_at after every row, as
# a backstop that keeps the time window meaningful on its own (e.g. if a worker
# ever runs where this process's registry can't see it).
STALE_JOB_MINUTES = 10


def _reap_stale_jobs(conn: sqlite3.Connection) -> int:
    """Fail in-flight jobs with no live worker whose heartbeat has gone quiet
    past STALE_JOB_MINUTES: the worker died, or the task never got scheduled.

    Any observer (a status poll, a new job request, a page mount) can flip these
    to failed so nothing polls a zombie forever. A job whose worker is running
    in this process is skipped however long it has been quiet, so a slow call
    can never get a live job reaped and a duplicate run started beside it.
    Returns the number of jobs reaped. Caller commits.

    Reads before writing: polls are frequent and almost never find anything
    stale, so they shouldn't need the write lock at all. A WAL read never
    blocks; an unconditional UPDATE here once made every poll 500 with
    "database is locked" while the worker was mid-write.
    """
    live = live_job_ids()
    stale_where = (
        "WHERE status IN ('queued','running') "
        f"AND updated_at < datetime('now', '-{STALE_JOB_MINUTES} minutes')"
        + (f" AND id NOT IN ({','.join('?' * len(live))})" if live else "")
    )
    if conn.execute(f"SELECT 1 FROM annotation_jobs {stale_where} LIMIT 1", live).fetchone() is None:
        return 0
    cur = conn.execute(
        "UPDATE annotation_jobs SET status='failed', "
        f"error='Went stale without a worker', updated_at=datetime('now') {stale_where}",
        live,
    )
    return cur.rowcount


def reap_interrupted_jobs(conn: sqlite3.Connection) -> int:
    """Fail every queued/running job. Called at startup: this is a
    single-process app, so any job still marked in-flight when the process
    boots was orphaned by a restart and will never finish. Left alone it would
    satisfy start_auto_annotate_job's single-job guard forever and block all
    future annotation runs."""
    cur = conn.execute(
        "UPDATE annotation_jobs SET status='failed', "
        "error='Interrupted by a server restart', updated_at=datetime('now') "
        "WHERE status IN ('queued','running')"
    )
    conn.commit()
    return cur.rowcount


@tracks_live_job
def _run_annotation_job(
    job_id: str,
    statement_id: str | None,
    transaction_ids: list[str] | None,
) -> None:
    conn = _open_job_connection()
    try:
        conn.execute(
            "UPDATE annotation_jobs SET status='running', updated_at=datetime('now') WHERE id = ?",
            (job_id,),
        )
        conn.commit()

        last_commit = 0.0

        def progress(processed: int, total: int) -> None:
            nonlocal last_commit
            # Write at most ~once per second, and commit every write it makes.
            # A throttled UPDATE left uncommitted would hold the write lock into
            # the next slow LLM call and block every other writer.
            now = time.monotonic()
            if now - last_commit < 1.0 and processed != total:
                return
            conn.execute(
                "UPDATE annotation_jobs SET processed=?, total=?, updated_at=datetime('now') WHERE id = ?",
                (processed, total, job_id),
            )
            conn.commit()
            last_commit = now

        result = auto_annotate(conn, statement_id, transaction_ids, progress_cb=progress)
        conn.execute(
            """UPDATE annotation_jobs
               SET status='completed', processed=?, total=?, result=?, updated_at=datetime('now')
               WHERE id = ?""",
            (result.total_processed, result.total_processed, result.model_dump_json(), job_id),
        )
        conn.commit()
    except Exception as e:
        logger.exception("annotation job failed | job=%s", job_id)
        conn.rollback()
        conn.execute(
            "UPDATE annotation_jobs SET status='failed', error=?, updated_at=datetime('now') WHERE id = ?",
            (str(e), job_id),
        )
        conn.commit()
    finally:
        conn.close()


@router.post("/auto-annotate/jobs", status_code=202)
def start_auto_annotate_job(
    body: AutoAnnotateRequest,
    background_tasks: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Start auto-annotation in the background; poll GET /annotations/jobs/{id} for progress.

    Single-user app: only one annotation job may be in flight at a time. A second
    request (e.g. a double-click or a click from another open tab) re-attaches to
    the running job instead of starting a duplicate, which would burn duplicate LLM
    calls and could overwrite a manual label created mid-run.
    """
    # Reap + guard + insert must be atomic against a concurrent request (a
    # double-click or a second tab). SQLite serializes writes but not a
    # read-then-write across two connections: without a write lock held up
    # front, both requests could see no in-flight job and both insert +
    # schedule, which is the duplicate-LLM-burn the single-job guard exists to
    # prevent. BEGIN IMMEDIATE takes the write lock now, so the second request
    # blocks (busy_timeout) until the first commits and then sees its row.
    scheduled_job_id: str | None = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        # An in-flight row with a quiet heartbeat is an orphan (worker died or
        # the task never got scheduled), not a running job. Fail it here rather
        # than re-attach, or it would block every future run until restart.
        _reap_stale_jobs(conn)

        inflight = conn.execute(
            "SELECT id, status, kind, statement_id FROM annotation_jobs "
            "WHERE status IN ('queued','running') ORDER BY created_at LIMIT 1"
        ).fetchone()
        if inflight is not None:
            conn.commit()
            # Say it's a re-attach and to what: the in-flight job may be a
            # different kind (a re-embed) or another statement's run, and the
            # caller must not present its result as the run it asked for.
            return {
                "job_id": inflight["id"],
                "status": inflight["status"],
                "kind": inflight["kind"],
                "statement_id": inflight["statement_id"],
                "attached": True,
            }

        job_id = str(ulid.ULID())
        conn.execute(
            "INSERT INTO annotation_jobs (id, statement_id) VALUES (?, ?)",
            (job_id, body.statement_id),
        )
        conn.commit()
        scheduled_job_id = job_id
    except Exception:
        conn.rollback()
        raise

    # Schedule the worker only after the row is committed and the lock released,
    # so the background task's own connection can't deadlock on our write lock.
    background_tasks.add_task(
        _run_annotation_job, scheduled_job_id, body.statement_id, body.transaction_ids
    )
    return {"job_id": scheduled_job_id, "status": "queued"}


@router.get("/jobs")
def list_active_annotation_jobs(active: int = 0, conn: sqlite3.Connection = Depends(get_db)):
    """List annotation jobs; with ?active=1 return only the in-flight one (if any).

    Lets the UI re-attach its progress card after a page reload: the server keeps
    working through a background job, but React job state is lost on refresh. Reaps
    stale jobs first so a refresh never resurfaces a zombie's progress card.
    """
    if _reap_stale_jobs(conn):
        conn.commit()
    where = "WHERE status IN ('queued','running')" if active else ""
    rows = conn.execute(
        f"SELECT * FROM annotation_jobs {where} ORDER BY created_at DESC"
    ).fetchall()
    jobs = []
    for row in rows:
        job = dict(row)
        if job.get("result"):
            job["result"] = json.loads(job["result"])
        jobs.append(job)
    return jobs


@router.get("/jobs/{job_id}")
def get_annotation_job(job_id: str, conn: sqlite3.Connection = Depends(get_db)):
    # Apply the stale-heartbeat check so a client polling an orphaned job (worker
    # died, server still up) sees it resolve to 'failed' instead of spinning
    # forever on a zombie 'running' row.
    if _reap_stale_jobs(conn):
        conn.commit()
    row = conn.execute("SELECT * FROM annotation_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job = dict(row)
    if job.get("result"):
        job["result"] = json.loads(job["result"])
    return job


@router.post("", status_code=201)
def create_annotation(
    body: AnnotationCreate,
    conn: sqlite3.Connection = Depends(get_db),
):
    category_id, subcategory_id = resolve_category_ids(conn, body.category, body.subcategory)
    if category_id is None:
        raise HTTPException(status_code=422, detail=f"Unknown category: {body.category}")
    if body.subcategory and subcategory_id is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown subcategory under {body.category}: {body.subcategory}",
        )
    annotation = Annotation(
        transaction_id=body.transaction_id,
        merchant=body.merchant,
        category=body.category,
        subcategory=body.subcategory,
        tags=dump_string_list(body.tags),
        confidence=body.confidence,
        source=body.source,
    )
    inserted = insert_annotation(conn, annotation)
    if not inserted:
        # insert_annotation never overwrites; an existing annotation must be
        # edited via PATCH so its original_source/reasoning history survives.
        raise HTTPException(
            status_code=409,
            detail=f"Transaction {body.transaction_id} is already annotated; PATCH it to edit.",
        )
    conn.commit()
    # Embed immediately so this annotation can serve as a RAG donor (best-effort)
    embed_transaction(conn, annotation.transaction_id)
    response = _annotation_response(annotation)
    if body.source == "manual":
        response["reevaluated"] = reevaluate_after_teaching(conn, [annotation.transaction_id])
    return response


def _patch_from_body(conn: sqlite3.Connection, existing: dict, body: AnnotationPatch) -> dict:
    """The column changes a PATCH body asks for, validated against the taxonomy."""
    # Only fields the client actually sent are updated; an explicit null clears
    # the field (merchant/subcategory/tags are nullable, category/confidence are not).
    patch: dict = {}
    for field in ("merchant", "category", "subcategory", "tags", "confidence"):
        if field not in body.model_fields_set:
            continue
        value = getattr(body, field)
        if value is None and field in ("category", "confidence"):
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")
        if field == "tags":
            value = dump_string_list(value)
        patch[field] = value

    if "category" in patch or "subcategory" in patch:
        new_category = patch.get("category", existing["category"])
        new_subcategory = patch.get("subcategory", existing.get("subcategory"))
        category_id, subcategory_id = resolve_category_ids(conn, new_category, new_subcategory)
        # Strict only for values the client sent; inherited stale names (e.g. an
        # old LLM free-text subcategory) just leave the id NULL.
        if "category" in patch and category_id is None:
            raise HTTPException(status_code=422, detail=f"Unknown category: {new_category}")
        if "subcategory" in patch and new_subcategory and subcategory_id is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown subcategory under {new_category}: {new_subcategory}",
            )
        patch["category_id"] = category_id
        patch["subcategory_id"] = subcategory_id
    return patch


def _apply_correction(conn: sqlite3.Connection, existing: dict, patch: dict) -> None:
    """Record calibration feedback against the machine label, then apply. Caller commits."""
    if existing["source"] in _MODEL_SOURCES:
        record_feedback(conn, existing["source"], existing["category"], _classify_feedback(existing, patch))
    update_annotation(conn, existing["id"], patch)


def _apply_confirmation(conn: sqlite3.Connection, existing: dict) -> None:
    """A human vouches for the label as is. Caller commits."""
    if existing["source"] in _MODEL_SOURCES:
        record_feedback(conn, existing["source"], existing["category"], "confirmed")
    update_annotation(conn, existing["id"], {"confidence": 1.0})


@router.patch("/{annotation_id}")
def patch_annotation(
    annotation_id: str,
    body: AnnotationPatch,
    conn: sqlite3.Connection = Depends(get_db),
):
    existing = get_annotation(conn, annotation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Annotation not found")

    patch = _patch_from_body(conn, existing, body)
    if patch:
        _apply_correction(conn, existing, patch)
        conn.commit()
        # Corrected annotations are trusted donors — refresh the embedding (best-effort)
        embed_transaction(conn, existing["transaction_id"])

    updated = _as_response(get_annotation(conn, annotation_id))
    updated["reevaluated"] = reevaluate_after_teaching(conn, [existing["transaction_id"]]) if patch else []
    return updated


class ConfirmRequest(BaseModel):
    # The category the user saw and is vouching for. Machine labels can be
    # re-decided while a card is on screen; confirming must not stamp a label
    # the user never saw.
    category: str


@router.post("/{annotation_id}/confirm")
def confirm_annotation(
    annotation_id: str,
    body: ConfirmRequest,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Mark an annotation as confirmed by a human without changes.

    Records a 'confirmed' feedback event for the original (source, category),
    then sets confidence=1.0 and source='manual'. 409 when the stored category
    is no longer the one the user confirmed.
    """
    existing = get_annotation(conn, annotation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Annotation not found")
    if existing["category"] != body.category:
        raise HTTPException(status_code=409, detail=f"This transaction is now labeled {existing['category']}")

    _apply_confirmation(conn, existing)
    conn.commit()
    embed_transaction(conn, existing["transaction_id"])

    updated = _as_response(get_annotation(conn, annotation_id))
    updated["reevaluated"] = reevaluate_after_teaching(conn, [existing["transaction_id"]])
    return updated


class GroupLabel(BaseModel):
    category: str
    subcategory: str | None = None
    merchant: str | None = None
    tags: list[str] | None = None


class BulkLabelRequest(BaseModel):
    annotation_ids: list[str]
    # The label on the card the user answered: confirming the group sends the
    # card's own label, so no member is stamped with a label nobody saw.
    label: GroupLabel


@router.post("/bulk-label")
def bulk_label(body: BulkLabelRequest, conn: sqlite3.Connection = Depends(get_db)):
    """One review answer for a group of transactions (the review queue's
    "apply to all payments to this payee"). Each member gets the label with
    the calibration feedback its own PATCH would record: 'confirmed' where its
    machine label already matched, 'corrected'/'refined' where it didn't. Then
    the new labels re-decide the machine labels they could change."""
    try:
        category_id, subcategory_id = require_category_ids(conn, body.label.category, body.label.subcategory)
    except CategoryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    patch = {
        "category": body.label.category, "subcategory": body.label.subcategory,
        "category_id": category_id, "subcategory_id": subcategory_id, "confidence": 1.0,
    }
    if "merchant" in body.label.model_fields_set:
        patch["merchant"] = body.label.merchant
    if "tags" in body.label.model_fields_set:
        patch["tags"] = dump_string_list(body.label.tags)
    taught = []
    for annotation_id in dict.fromkeys(body.annotation_ids):
        existing = get_annotation(conn, annotation_id)
        _apply_correction(conn, existing, patch)
        taught.append(existing["transaction_id"])
    conn.commit()
    embed_transactions(conn, taught)
    return {"updated": len(taught), "reevaluated": reevaluate_after_teaching(conn, taught)}


class BulkCategoryRequest(BaseModel):
    transaction_ids: list[str]
    category: str
    subcategory: str | None = None


@router.post("/bulk-category")
def bulk_set_category(
    body: BulkCategoryRequest,
    background_tasks: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Set a category on many transactions at once (4.12).

    Each transaction is annotated as a manual label: an unannotated one gets a
    new manual annotation, an already-annotated one is corrected via
    update_annotation (preserving original_source/history). Confidence is 1.0 -
    a human explicitly chose this."""
    category_id, subcategory_id = resolve_category_ids(conn, body.category, body.subcategory)
    if category_id is None:
        raise HTTPException(status_code=422, detail=f"Unknown category: {body.category}")
    if body.subcategory and subcategory_id is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown subcategory under {body.category}: {body.subcategory}",
        )
    if not body.transaction_ids:
        return {"updated": 0, "skipped": []}

    # Ids come straight from the client; one that was deleted since the page
    # loaded would trip the annotations FK and 500 the whole batch.
    known: set[str] = set()
    for i in range(0, len(body.transaction_ids), 500):
        chunk = body.transaction_ids[i : i + 500]
        placeholders = ",".join("?" * len(chunk))
        known.update(
            r[0]
            for r in conn.execute(f"SELECT id FROM transactions WHERE id IN ({placeholders})", chunk)
        )
    to_update = [t for t in dict.fromkeys(body.transaction_ids) if t in known]
    skipped = [t for t in body.transaction_ids if t not in known]

    updated = 0
    for txn_id in to_update:
        existing = get_annotation_by_transaction(conn, txn_id)
        if existing is None:
            insert_annotation(conn, Annotation(
                transaction_id=txn_id,
                category=body.category,
                subcategory=body.subcategory,
                confidence=1.0,
                source="manual",
            ))
        else:
            if existing["source"] in _MODEL_SOURCES:
                record_feedback(conn, existing["source"], existing["category"], "corrected")
            update_annotation(conn, existing["id"], {
                "category": body.category,
                "subcategory": body.subcategory,
                "category_id": category_id,
                "subcategory_id": subcategory_id,
                "confidence": 1.0,
            })
        updated += 1

    conn.commit()
    background_tasks.add_task(_embed_in_background, to_update)
    return {"updated": updated, "skipped": skipped}


class ApplySimilarRequest(BaseModel):
    transaction_ids: list[str]


@router.get("/{annotation_id}/similar")
def similar_candidates(
    annotation_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Machine-labeled neighbours of this annotation's transaction, for bulk fix-up.

    A human correction is worth 5-20 corrections when propagated: return every
    machine-sourced annotation whose transaction is either ≥ apply_similar_floor
    cosine-similar or shares the same UPI counterparty identity. Human-sourced
    annotations are never offered for overwriting.
    """
    ann = get_annotation(conn, annotation_id)
    if ann is None:
        raise HTTPException(status_code=404, detail="Annotation not found")
    txn_row = conn.execute(
        "SELECT * FROM transactions WHERE id = ?", (ann["transaction_id"],)
    ).fetchone()
    if txn_row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    txn = dict(txn_row)

    candidate_ids: dict[str, float | None] = {}  # transaction_id → similarity

    try:
        vec = get_embedding_single(build_embed_text(txn))
        for m in find_similar(conn, vec, top_k=50, exclude_transaction_ids=[txn["id"]]):
            similarity = 1.0 - m["distance"]
            if similarity >= settings.apply_similar_floor:
                candidate_ids[m["transaction_id"]] = round(similarity, 4)
    except Exception as e:
        logger.warning("apply-similar | embedding unavailable, identity-only | %s", e)

    # Same-counterparty candidates via the indexed, resolved counterparty_key
    # (truncation variants of one payee share it; see pipeline/counterparty.py).
    identity = txn["counterparty_key"]
    if identity is not None:
        rows = conn.execute(
            "SELECT id FROM transactions WHERE counterparty_key = ? AND id != ?",
            (identity, txn["id"]),
        ).fetchall()
        for r in rows:
            candidate_ids.setdefault(r["id"], None)

    if not candidate_ids:
        return []

    placeholders = ",".join("?" * len(candidate_ids))
    rows = conn.execute(
        f"""
        SELECT a.id AS annotation_id, a.category, a.subcategory, a.merchant, a.source,
               a.confidence, t.id AS transaction_id, t.txn_date, t.amount,
               t.debit_credit, t.raw_description
        FROM annotations a JOIN transactions t ON t.id = a.transaction_id
        WHERE t.id IN ({placeholders})
        """,
        list(candidate_ids),
    ).fetchall()

    out = []
    for r in rows:
        if r["source"] not in _MACHINE_SOURCES:
            continue
        item = dict(r)
        item["similarity"] = candidate_ids.get(r["transaction_id"])
        item["differs"] = (
            r["category"] != ann["category"] or r["subcategory"] != ann.get("subcategory")
        )
        out.append(item)
    # Different-label candidates first (the ones worth fixing), then by similarity.
    out.sort(key=lambda x: (not x["differs"], -(x["similarity"] or 0.0)))
    return out


@router.post("/{annotation_id}/apply-to-similar")
def apply_to_similar(
    annotation_id: str,
    body: ApplySimilarRequest,
    background_tasks: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Copy this annotation's label onto the selected machine-labeled transactions.

    Each target is treated as a human decision (the user explicitly selected it):
    feedback is recorded against the target's machine source, source flips to
    manual (original_source preserved by update_annotation), confidence 1.0.
    """
    donor = get_annotation(conn, annotation_id)
    if donor is None:
        raise HTTPException(status_code=404, detail="Annotation not found")

    applied = 0
    skipped = 0
    for txn_id in body.transaction_ids:
        target = get_annotation_by_transaction(conn, txn_id)
        if target is None or target["source"] not in _MACHINE_SOURCES:
            skipped += 1
            continue
        patch = {
            "category": donor["category"],
            "subcategory": donor.get("subcategory"),
            "merchant": donor.get("merchant"),
            "tags": donor.get("tags"),
            "confidence": 1.0,
        }
        feedback_type = _classify_feedback(target, patch)
        record_feedback(conn, target["source"], target["category"], feedback_type)
        update_annotation(conn, target["id"], patch)
        applied += 1
    conn.commit()
    background_tasks.add_task(_embed_in_background, list(body.transaction_ids))
    return {"applied": applied, "skipped": skipped}


@router.get("/run-summary")
def run_summary_endpoint(
    statement_id: str | None = None,
    threshold: float | None = Query(None, ge=0.0, le=1.0, description="review-bar override for a live preview"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Run-level aggregation over stored reasoning traces (dev-mode insight surface).

    Stage funnel, similarity/confidence distributions drawn against their
    thresholds, and near-miss lists. Gated behind dev mode: without it, the trace
    detail this summarizes is never shown, so the summary would be misleading.
    Optional statement_id scopes it to one statement. `threshold` previews a
    candidate review bar (the Settings slider) without persisting it; when omitted
    the stored runtime threshold is used.
    """
    from finance_copilot.pipeline.run_summary import run_summary

    if not get_dev_mode(conn):
        raise HTTPException(status_code=404, detail="Developer mode is off")
    effective = threshold if threshold is not None else get_confidence_threshold(conn)
    return run_summary(conn, statement_id, threshold=effective)


@router.get("/learned-rules")
def learned_rules(conn: sqlite3.Connection = Depends(get_db)):
    """Merchant memories the pipeline would apply deterministically right now.

    Transparency for the learned-rule stage: each entry is a counterparty the
    user has verified enough times at high purity. Computed live from present-day
    annotations (no stored table), so correcting transactions is how a user
    changes or retires one.
    """
    from finance_copilot.db.queries.learned_rules import list_learned_rules

    return [
        {
            "counterparty_key": r.counterparty_key,
            "category": r.category,
            "subcategory": r.subcategory,
            "merchant": r.merchant,
            "support": r.support,
            "total": r.total,
            "purity": r.purity,
            "direction": r.direction,
        }
        for r in list_learned_rules(conn)
    ]


@router.get("/accuracy")
def accuracy(conn: sqlite3.Connection = Depends(get_db)):
    """Per-category review accuracy (weakest first) with sample sizes (4.19).

    Built from human feedback on model labels; the dashboard shows the shakiest
    categories so the user knows where their corrections help most."""
    return category_accuracy(conn)


@router.delete("/learned-rules/{counterparty_key:path}", status_code=204)
def dismiss_learned_rule(
    counterparty_key: str,
    direction: Literal["debit", "credit"],
    conn: sqlite3.Connection = Depends(get_db),
):
    """Dismiss one learned rule (payee and direction) so it stops firing and drops
    off Settings. Learned rules are recomputed from verified labels, so the
    dismissal is a sticky suppression. Idempotent."""
    from finance_copilot.db.queries.learned_rules import suppress_learned_rule

    suppress_learned_rule(conn, counterparty_key, direction)
    conn.commit()


@router.get("/review-queue")
def review_queue(conn: sqlite3.Connection = Depends(get_db)):
    dev_mode = get_dev_mode(conn)
    items = group_review_queue(list_review_queue(conn, get_confidence_threshold(conn)))
    for item in items:
        item["tags"] = parse_string_list(item.get("tags"))
        # Dev mode: surface the captured reasoning trace; otherwise drop the raw
        # column so the UI never sees it. Older rows have reasoning=NULL → None.
        raw = item.pop("reasoning", None)
        if dev_mode:
            try:
                item["reasoning"] = json.loads(raw) if raw else None
            except (TypeError, json.JSONDecodeError):
                item["reasoning"] = None
    return items


@router.get("/review-queue/snoozed-count")
def review_queue_snoozed_count(conn: sqlite3.Connection = Depends(get_db)):
    """How many below-threshold items the user has skipped (snoozed). The queue
    surfaces this so snoozed items are never fully hidden and can be restored."""
    return {"count": count_snoozed(conn, get_confidence_threshold(conn))}


@router.post("/review-queue/rescore", status_code=200)
def review_queue_rescore(conn: sqlite3.Connection = Depends(get_db)):
    """Re-decide every queued item with the deterministic stages (no LLM), so
    items that current rules, learned rules, or donors can now settle leave the
    queue. Also runs after a re-embed job."""
    changed = rescore_review_queue(conn)
    return {"resolved": sum(c["resolved"] for c in changed), "changed": len(changed)}


@router.post("/review-queue/unsnooze", status_code=200)
def review_queue_unsnooze(conn: sqlite3.Connection = Depends(get_db)):
    """Bring every snoozed item back into the review queue."""
    cleared = unsnooze_all(conn)
    conn.commit()
    return {"restored": cleared}


@router.post("/{annotation_id}/skip", status_code=200)
def skip_annotation(annotation_id: str, conn: sqlite3.Connection = Depends(get_db)):
    """Snooze one annotation out of the review queue - a skip that persists across
    sessions instead of silently reappearing (4.15)."""
    if not snooze_annotation(conn, annotation_id):
        raise HTTPException(status_code=404, detail=f"Annotation {annotation_id} not found")
    conn.commit()
    return {"snoozed": True}


def _classify_feedback(existing: dict, patch: dict) -> str:
    """Detect whether a patch represents a confirmation, refinement, or correction.

    - corrected: category field changed
    - refined:   category unchanged but subcategory/merchant/tags changed
    - confirmed: no meaningful fields changed (e.g. only confidence patched)
    """
    new_category = patch.get("category")
    if new_category is not None and new_category != existing["category"]:
        return "corrected"
    for field in ("subcategory", "merchant", "tags"):
        if field in patch and patch[field] != existing.get(field):
            return "refined"
    return "confirmed"


def _annotation_response(annotation: Annotation) -> dict:
    d = annotation.model_dump()
    d["tags"] = parse_string_list(annotation.tags)
    return d


def _as_response(row: dict) -> dict:
    row["tags"] = parse_string_list(row.get("tags"))
    return row
