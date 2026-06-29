"""Settings: database path, Ollama URL, confidence threshold, and related configuration."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_path: str = str(Path(__file__).parent.parent / "data" / "finance.db")
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:4b"
    confidence_threshold: float = 0.85
    ollama_embedding_model: str = "nomic-embed-text"
    rag_direct_threshold: float = 0.92
    rag_top_k: int = 5
    rag_similarity_floor: float = 0.65
    rag_agreement_exponent: float = 0.3
    rag_margin_safe: float = 0.08
    llm_confidence_dampen: float = 0.85
    llm_confidence_dampen_rag: float = 0.92
    # Cap on confidence when the rag_prompted LLM picks a category that appears in
    # none of the retrieved examples — keeps unsupported guesses out of the
    # auto-accepted set and routes them to the review queue instead.
    rag_offexample_confidence_cap: float = 0.5
    # Vote weight given to machine-sourced donors (llm/rag_*) relative to a
    # human-verified donor (manual/rule/imported, weight 1.0). Past machine
    # guesses are weak evidence and must not out-vote a human label.
    rag_machine_donor_weight: float = 0.25
    # Reject/defer band: when the trusted vote has no clear winner (the top
    # category's share of the trusted weighted vote is below this), cap confidence
    # below the review threshold so the transaction is routed to a human instead
    # of being auto-labeled. Selective-classification policy, user-agnostic.
    rag_consensus_floor: float = 0.6
    rag_defer_confidence_cap: float = 0.5
    # The defer band only fires when the LLM is itself uncertain (below this raw
    # confidence). A confident, merchant-grounded LLM answer (e.g. 'Zomato') is
    # never deferred just because the amount-driven neighbor vote is split.
    rag_defer_llm_confidence: float = 0.85
    api_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    # Last-segment values in UPI descriptions that carry no meaningful note.
    # Extend this list as you encounter new noise patterns — no code changes needed.
    upi_noise_keywords: list[str] = [
        "UPI", "NEFT", "IMPS", "RTGS", "NA", "NO REMARKS", "N/A", "NONE", "-",
    ]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
