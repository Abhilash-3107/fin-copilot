"""Pydantic models for Annotation and category-related shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

import ulid
from pydantic import BaseModel, Field


class Annotation(BaseModel):
    id: str = Field(default_factory=lambda: str(ulid.ULID()))
    transaction_id: str
    merchant: Optional[str] = None
    category: str
    subcategory: Optional[str] = None
    tags: str = ""  # comma-separated, matches DB column
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: Literal["manual", "model", "rule", "rag_direct", "rag_prompted", "llm", "imported"]
    annotated_at: Optional[datetime] = None


class AnnotationCreate(BaseModel):
    transaction_id: str
    merchant: Optional[str] = None
    category: str
    subcategory: Optional[str] = None
    tags: list[str] = []
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: Literal["manual", "model", "rule", "rag_direct", "rag_prompted", "llm", "imported"]


class AnnotationPatch(BaseModel):
    merchant: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    tags: Optional[list[str]] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class AutoAnnotateResult(BaseModel):
    total_processed: int
    rule_matched: int
    rag_direct_annotated: int = 0
    rag_prompted_annotated: int = 0
    llm_annotated: int
    llm_failed: int
    low_confidence: int       # annotations below settings.confidence_threshold
    already_annotated: int    # transactions skipped because already annotated
