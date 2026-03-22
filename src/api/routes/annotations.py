"""Annotation routes: create, patch, and review-queue listing."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_db
from src.config import settings
from src.db.queries.annotations import (
    get_annotation,
    insert_annotation,
    list_review_queue,
    update_annotation,
)
from src.models.annotation import Annotation, AnnotationCreate, AnnotationPatch

router = APIRouter()


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
        update_annotation(conn, annotation_id, patch)
        conn.commit()

    updated = get_annotation(conn, annotation_id)
    return _as_response(updated)


@router.get("/review-queue")
def review_queue(conn: sqlite3.Connection = Depends(get_db)):
    items = list_review_queue(conn, settings.confidence_threshold)
    for item in items:
        item["tags"] = [t for t in item.get("tags", "").split(",") if t]
    return items


def _annotation_response(annotation: Annotation) -> dict:
    d = annotation.model_dump()
    d["tags"] = [t for t in annotation.tags.split(",") if t]
    return d


def _as_response(row: dict) -> dict:
    row["tags"] = [t for t in row.get("tags", "").split(",") if t]
    return row
