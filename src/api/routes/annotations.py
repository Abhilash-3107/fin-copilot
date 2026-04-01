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

router = APIRouter()


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

    patch: dict = {}
    if body.merchant is not None:
        patch["merchant"] = body.merchant
    if body.category is not None:
        patch["category"] = body.category
    if body.subcategory is not None:
        patch["subcategory"] = body.subcategory
    if body.tags is not None:
        patch["tags"] = ",".join(body.tags)
    if body.confidence is not None:
        patch["confidence"] = body.confidence

    if patch:
        # Record feedback before updating — use original source + category.
        # Only track feedback for pipeline sources that use dampening.
        if existing["source"] in ("llm", "rag_prompted"):
            feedback_type = _classify_feedback(existing, patch)
            record_feedback(conn, existing["source"], existing["category"], feedback_type)

        update_annotation(conn, annotation_id, patch)
        conn.commit()

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

    if existing["source"] in ("llm", "rag_prompted"):
        record_feedback(conn, existing["source"], existing["category"], "confirmed")

    update_annotation(conn, annotation_id, {"confidence": 1.0})
    conn.commit()

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
