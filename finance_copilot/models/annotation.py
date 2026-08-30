"""Pydantic models for Annotation and category-related shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

import ulid
from pydantic import BaseModel, Field


class TraceNeighbour(BaseModel):
    """One RAG neighbour as surfaced in the dev-mode reasoning trace."""
    transaction_id: str
    raw_description: str | None = None
    category: str | None = None
    source: str | None = None
    distance: float
    similarity: float  # 1.0 - distance


class TraceExample(BaseModel):
    """One few-shot example as it was sent to the rag_prompted LLM call.

    Distinct from TraceNeighbour: neighbours are the deduped vote donors, examples
    are what the LLM actually saw (wide-pool + diversity selection + source ordering
    can make the two sets differ).
    """
    transaction_id: str | None = None
    raw_description: str | None = None
    category: str | None = None
    subcategory: str | None = None
    source: str | None = None


class ReasoningTrace(BaseModel):
    """Why the pipeline chose what it did — captured at annotation time.

    Always stored (annotations.reasoning JSON column); dev mode only gates whether
    the API/UI surface it. This is a persistence format read long after settings
    change, so keep it strictly additive: every new field must be optional.

    All fields are optional so each stage fills only what applies.
    """
    stage: str  # "rule" | "learned_rule" | "rag_direct" | "rag_prompted" | "llm"
    final_confidence: float
    # Snapshot of the settings each measured value was gated against, taken at
    # annotation time (settings drift; the trace must stay self-explanatory).
    # Keys are setting names, e.g. {"rag_direct_threshold": 0.92, ...}.
    thresholds: dict[str, float] = Field(default_factory=dict)
    # Routing trail: why earlier stages fell through before this one decided,
    # e.g. ["rule: no match", "rag_direct: donor source 'llm' untrusted"].
    skips: list[str] = Field(default_factory=list)
    # The exact string that was embedded for retrieval (build_embed_text output).
    embed_text: str | None = None
    # RAG paths (rag_direct + rag_prompted)
    best_similarity: float | None = None
    neighbours: list[TraceNeighbour] = Field(default_factory=list)
    vote_category: str | None = None
    vote_share: float | None = None
    trusted_weight: float | None = None
    agreement_factor: float | None = None
    margin_factor: float | None = None
    caps_applied: list[str] = Field(default_factory=list)  # e.g. ["off_example", "defer"]
    # Counterparty recurrence prior (rag_prompted) — the late-fused out-of-band signal
    counterparty_prior_category: str | None = None
    counterparty_prior_probability: float | None = None
    counterparty_prior_n: int | None = None       # prior observations for this counterparty
    counterparty_prior_effect: str | None = None  # "rescue" | "tighten" | "neutral"
    # LLM paths (rag_prompted + llm)
    llm_reasoning: str | None = None      # the one-sentence "why" from the model
    raw_confidence: float | None = None   # before dampening
    dampening_factor: float | None = None
    calibration_bucket: str | None = None  # (source, category) feedback bucket, e.g. "llm/Food & Dining"
    # LLM call telemetry
    llm_model: str | None = None
    prompt_tokens: int | None = None        # prompt_eval_count / usage.prompt_tokens
    prompt_truncated: bool | None = None    # prompt_tokens ~ num_ctx → front-truncation likely
    verbalized_confidence: float | None = None  # the number the model wrote
    logprob_confidence: float | None = None     # token-logprob mass (when enabled/available)
    # Few-shot prompt content (rag_prompted)
    prompt_examples: list[TraceExample] = Field(default_factory=list)
    majority_category: str | None = None  # the hint passed to the LLM
    majority_count: int | None = None
    # rule path
    matched_rule: str | None = None


class Annotation(BaseModel):
    id: str = Field(default_factory=lambda: str(ulid.ULID()))
    transaction_id: str
    merchant: str | None = None
    category: str
    subcategory: str | None = None
    tags: str = ""  # JSON-array string, matches DB column (see queries.common helpers)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: Literal["manual", "model", "rule", "learned_rule", "rag_direct", "rag_prompted", "llm", "imported"]
    annotated_at: datetime | None = None
    reasoning: str | None = None  # JSON-serialized ReasoningTrace, dev mode only


class AnnotationCreate(BaseModel):
    transaction_id: str
    merchant: str | None = None
    category: str
    subcategory: str | None = None
    tags: list[str] = []
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: Literal["manual", "model", "rule", "learned_rule", "rag_direct", "rag_prompted", "llm", "imported"]


class AnnotationPatch(BaseModel):
    merchant: str | None = None
    category: str | None = None
    subcategory: str | None = None
    tags: list[str] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class AutoAnnotateResult(BaseModel):
    total_processed: int
    rule_matched: int
    learned_rule_annotated: int = 0
    rag_direct_annotated: int = 0
    rag_prompted_annotated: int = 0
    llm_annotated: int
    llm_failed: int
    low_confidence: int       # annotations below settings.confidence_threshold
    already_annotated: int    # transactions skipped because already annotated
