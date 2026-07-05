"""Annotation routes: create, patch, confirm, review-queue listing, and background jobs."""
from __future__ import annotations

import json
import logging
import sqlite3
import time

import ulid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from src.api.deps import get_db
from src.config import settings
from src.db.queries.annotations import (
    get_annotation,
    get_annotation_by_transaction,
    insert_annotation,
    list_review_queue,
    update_annotation,
)
from src.db.queries.embeddings import find_similar
from src.pipeline.counterparty import normalize_identity
from src.pipeline.embed import build_embed_text, get_embedding_single
from src.db.queries.app_settings import get_dev_mode
from src.db.queries.categories import resolve_category_ids
from src.db.queries.common import dump_string_list, parse_string_list
from src.db.queries.feedback_stats import record_feedback
from src.models.annotation import Annotation, AnnotationCreate, AnnotationPatch, AutoAnnotateResult
from src.pipeline.annotate import auto_annotate
from src.pipeline.embed import embed_transaction

router = APIRouter()

# Pipeline sources whose outcomes feed calibration. Corrections to rag_direct and
# rule annotations are tracked too: they tune thresholds and expose bad donors,
# even though only llm/rag_prompted confidences are dampened today.
_MODEL_SOURCES = ("llm", "rag_prompted", "rag_direct", "rule", "learned_rule")


class AutoAnnotateRequest(BaseModel):
    statement_id: str | None = None
    transaction_ids: list[str] | None = None


@router.post("/auto-annotate", response_model=AutoAnnotateResult)
def auto_annotate_endpoint(
    body: AutoAnnotateRequest,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Synchronous annotation — fine for scripts/small batches. The UI uses the job flow."""
    return auto_annotate(conn, body.statement_id, body.transaction_ids)


def _open_job_connection() -> sqlite3.Connection:
    """Background tasks outlive the request's connection; open a fresh one."""
    from src.db.connection import get_connection

    return get_connection()


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
            conn.execute(
                "UPDATE annotation_jobs SET processed=?, total=?, updated_at=datetime('now') WHERE id = ?",
                (processed, total, job_id),
            )
            # Commit at most ~once per second; the pipeline's batch commits flush the rest
            now = time.monotonic()
            if now - last_commit >= 1.0 or processed == total:
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
    inflight = conn.execute(
        "SELECT id, status FROM annotation_jobs WHERE status IN ('queued','running') "
        "ORDER BY created_at LIMIT 1"
    ).fetchone()
    if inflight is not None:
        return {"job_id": inflight["id"], "status": inflight["status"]}

    job_id = str(ulid.ULID())
    conn.execute(
        "INSERT INTO annotation_jobs (id, statement_id) VALUES (?, ?)",
        (job_id, body.statement_id),
    )
    conn.commit()
    background_tasks.add_task(_run_annotation_job, job_id, body.statement_id, body.transaction_ids)
    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs/{job_id}")
def get_annotation_job(job_id: str, conn: sqlite3.Connection = Depends(get_db)):
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
    insert_annotation(conn, annotation)
    conn.commit()
    # Embed immediately so this annotation can serve as a RAG donor (best-effort)
    embed_transaction(conn, annotation.transaction_id)
    return _annotation_response(annotation)


@router.patch("/{annotation_id}")
def patch_annotation(
    annotation_id: str,
    body: AnnotationPatch,
    conn: sqlite3.Connection = Depends(get_db),
):
    existing = get_annotation(conn, annotation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Annotation not found")

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

    if patch:
        # Record feedback before updating — use original source + category.
        if existing["source"] in _MODEL_SOURCES:
            feedback_type = _classify_feedback(existing, patch)
            record_feedback(conn, existing["source"], existing["category"], feedback_type)

        update_annotation(conn, annotation_id, patch)
        conn.commit()
        # Corrected annotations are trusted donors — refresh the embedding (best-effort)
        embed_transaction(conn, existing["transaction_id"])

    updated = get_annotation(conn, annotation_id)
    return _as_response(updated)


@router.post("/{annotation_id}/confirm")
def confirm_annotation(
    annotation_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Mark an annotation as confirmed by a human without changes.

    Records a 'confirmed' feedback event for the original (source, category),
    then sets confidence=1.0 and source='manual'.
    """
    existing = get_annotation(conn, annotation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Annotation not found")

    if existing["source"] in _MODEL_SOURCES:
        record_feedback(conn, existing["source"], existing["category"], "confirmed")

    update_annotation(conn, annotation_id, {"confidence": 1.0})
    conn.commit()
    embed_transaction(conn, existing["transaction_id"])

    updated = get_annotation(conn, annotation_id)
    return _as_response(updated)


class ApplySimilarRequest(BaseModel):
    transaction_ids: list[str]


_MACHINE_SOURCES = {"llm", "rag_prompted", "rag_direct", "learned_rule"}


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

    # Same-counterparty candidates via the indexed counterparty_key (migration 017),
    # not a full scan of every UPI row. The Python-side normalize_identity check
    # stays as an exactness guard for the few matched rows, matching
    # counterparty_history() so the two identity paths never diverge.
    identity = txn.get("counterparty_key") or normalize_identity(txn.get("raw_description"))
    if identity is not None:
        rows = conn.execute(
            "SELECT id, raw_description FROM transactions WHERE counterparty_key = ? AND id != ?",
            (identity, txn["id"]),
        ).fetchall()
        for r in rows:
            if normalize_identity(r["raw_description"]) == identity:
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
    # Re-embed the corrected rows so they become trusted donors (best-effort).
    for txn_id in body.transaction_ids:
        embed_transaction(conn, txn_id)
    return {"applied": applied, "skipped": skipped}


@router.get("/run-summary")
def run_summary_endpoint(
    statement_id: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Run-level aggregation over stored reasoning traces (dev-mode insight surface).

    Stage funnel, similarity/confidence distributions drawn against their
    thresholds, and near-miss lists. Gated behind dev mode: without it, the trace
    detail this summarizes is never shown, so the summary would be misleading.
    Optional statement_id scopes it to one statement.
    """
    from src.pipeline.run_summary import run_summary

    if not get_dev_mode(conn):
        raise HTTPException(status_code=404, detail="Developer mode is off")
    return run_summary(conn, statement_id)


@router.get("/learned-rules")
def learned_rules(conn: sqlite3.Connection = Depends(get_db)):
    """Merchant memories the pipeline would apply deterministically right now.

    Transparency for the learned-rule stage: each entry is a counterparty the
    user has verified enough times at high purity. Computed live from present-day
    annotations (no stored table), so correcting transactions is how a user
    changes or retires one.
    """
    from src.db.queries.learned_rules import list_learned_rules

    return [
        {
            "counterparty_key": r.counterparty_key,
            "category": r.category,
            "subcategory": r.subcategory,
            "merchant": r.merchant,
            "support": r.support,
            "total": r.total,
            "purity": r.purity,
        }
        for r in list_learned_rules(conn)
    ]


@router.delete("/learned-rules/{counterparty_key:path}", status_code=204)
def dismiss_learned_rule(counterparty_key: str, conn: sqlite3.Connection = Depends(get_db)):
    """Dismiss a learned merchant rule so it stops firing and drops off Settings.

    Learned rules have no stored row (they're recomputed from verified labels), so
    the dismissal is a sticky suppression keyed by counterparty_key. Typically used
    once the user has added that counterparty to People and the person rule takes
    over. Idempotent — dismissing an already-dismissed key is a no-op.
    """
    from src.db.queries.learned_rules import suppress_learned_rule

    suppress_learned_rule(conn, counterparty_key)
    conn.commit()


@router.get("/review-queue")
def review_queue(conn: sqlite3.Connection = Depends(get_db)):
    dev_mode = get_dev_mode(conn)
    items = list_review_queue(conn, settings.confidence_threshold)
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
