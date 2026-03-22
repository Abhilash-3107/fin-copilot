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
    source: Literal["manual", "model", "imported"]
    annotated_at: Optional[datetime] = None


class AnnotationCreate(BaseModel):
    transaction_id: str
    merchant: Optional[str] = None
    category: str
    subcategory: Optional[str] = None
    tags: list[str] = []
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: Literal["manual", "model", "imported"]


class AnnotationPatch(BaseModel):
    merchant: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    tags: Optional[list[str]] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
