"""Structured output schema for Qwen (AnnotationResponse) matching the annotation prompt JSON."""
from __future__ import annotations

from pydantic import BaseModel, Field


class AnnotationResponse(BaseModel):
    merchant: str | None
    category: str
    subcategory: str | None
    tags: list[str] = []
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
