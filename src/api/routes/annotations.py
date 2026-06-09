"""Annotation routes: create, patch, confirm, and review-queue listing."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.deps import get_db
from src.config import settings
from src.db.queries.annotations import (
    get_annotation,
    insert_annotation,
    list_review_queue,
    update_annotation,
)
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
    return auto_annotate(conn, body.statement_id, body.transaction_ids)


@router.post("", status_code=201)
def create_annotation(
    body: AnnotationCreate,
    conn: sqlite3.Connection = Depends(get_db),
):
    annotation = Annotation(
        transaction_id=body.transaction_id,
        merchant=body.merchant,
        category=body.category,
        subcategory=body.subcategory,
        tags=",".join(body.tags),
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
            value = ",".join(value) if value else ""
        patch[field] = value

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
        item["tags"] = [t for t in item.get("tags", "").split(",") if t]
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
    d["tags"] = [t for t in annotation.tags.split(",") if t]
    return d


def _as_response(row: dict) -> dict:
    row["tags"] = [t for t in row.get("tags", "").split(",") if t]
    return row
