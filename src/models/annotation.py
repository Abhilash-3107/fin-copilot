"""Pydantic models for Annotation and category-related shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

import ulid
from pydantic import BaseModel, Field


class TraceNeighbour(BaseModel):
    """One RAG neighbour as surfaced in the dev-mode reasoning trace."""
    transaction_id: str
    raw_description: Optional[str] = None
    category: Optional[str] = None
    source: Optional[str] = None
    distance: float
    similarity: float  # 1.0 - distance


class ReasoningTrace(BaseModel):
    """Why the pipeline chose what it did — captured at annotation time for dev mode.

    All fields are optional so each stage fills only what applies. Serialized to the
    annotations.reasoning JSON column (only when settings.dev_mode is on).
    """
    stage: str  # "rule" | "rag_direct" | "rag_prompted" | "llm"
    final_confidence: float
    # RAG paths (rag_direct + rag_prompted)
    best_similarity: Optional[float] = None
    neighbours: list[TraceNeighbour] = Field(default_factory=list)
    vote_category: Optional[str] = None
    vote_share: Optional[float] = None
    trusted_weight: Optional[float] = None
    agreement_factor: Optional[float] = None
    margin_factor: Optional[float] = None
    caps_applied: list[str] = Field(default_factory=list)  # e.g. ["off_example", "defer"]
    # Counterparty recurrence prior (rag_prompted) — the late-fused out-of-band signal
    counterparty_prior_category: Optional[str] = None
    counterparty_prior_probability: Optional[float] = None
    counterparty_prior_n: Optional[int] = None       # prior observations for this counterparty
    counterparty_prior_effect: Optional[str] = None  # "rescue" | "tighten" | "neutral"
    # LLM paths (rag_prompted + llm)
    llm_reasoning: Optional[str] = None      # the one-sentence "why" from the model
    raw_confidence: Optional[float] = None   # before dampening
    dampening_factor: Optional[float] = None
    # rule path
    matched_rule: Optional[str] = None


class Annotation(BaseModel):
    id: str = Field(default_factory=lambda: str(ulid.ULID()))
    transaction_id: str
    merchant: Optional[str] = None
    category: str
    subcategory: Optional[str] = None
    tags: str = ""  # JSON-array string, matches DB column (see queries.common helpers)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: Literal["manual", "model", "rule", "rag_direct", "rag_prompted", "llm", "imported"]
    annotated_at: Optional[datetime] = None
    reasoning: Optional[str] = None  # JSON-serialized ReasoningTrace, dev mode only


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
