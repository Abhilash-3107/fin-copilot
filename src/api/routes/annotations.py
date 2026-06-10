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
    insert_annotation,
    list_review_queue,
    update_annotation,
)
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
_MODEL_SOURCES = ("llm", "rag_prompted", "rag_direct", "rule")


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
    """Start auto-annotation in the background; poll GET /annotations/jobs/{id} for progress."""
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


@router.get("/review-queue")
def review_queue(conn: sqlite3.Connection = Depends(get_db)):
    items = list_review_queue(conn, settings.confidence_threshold)
    for item in items:
        item["tags"] = parse_string_list(item.get("tags"))
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
