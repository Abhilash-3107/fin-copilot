"""Settings: database path, Ollama URL, confidence threshold, and related configuration."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_path: str = str(Path(__file__).parent.parent / "data" / "finance.db")
    # --- BYOM: pluggable LLM provider ---
    # "ollama": native Ollama API (default; structured output + logprobs).
    # "openai": any OpenAI-compatible /chat/completions endpoint (LM Studio,
    #           vLLM, OpenRouter, OpenAI itself) via llm_base_url + llm_api_key.
    # "none":   AI disabled; the pipeline degrades to rules + RAG-direct and
    #           routes everything else to the review queue.
    llm_provider: str = "ollama"
    llm_base_url: str = ""  # e.g. https://api.openai.com/v1 (openai provider only)
    llm_api_key: str = ""
    llm_model: str = ""  # openai-provider model name; ollama uses ollama_model
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
    # --- Counterparty recurrence prior (late-fused out-of-band signal) ---
    # Empirical-Bayes pseudo-count: the weight of the uninformed base rate relative
    # to observed labels. Higher = more evidence needed before a counterparty's own
    # history dominates. With this >0, a single observation can't pin probability to
    # 1.0 — recurrence (several consistent labels) is required to clear the floor.
    counterparty_prior_weight: float = 2.0
    # Minimum prior observations before the recurrence prior may influence routing.
    # Below this the prior is inert → cold-start / first-time counterparties behave
    # exactly like today. Tuned on the Dec–Mar 2026 causal backtest sweep
    # (scripts/backtest_counterparty_prior.py --sweep): min_obs=2, floor=0.65 gave
    # the best precision (~85%) at comparable coverage. The ~85% ceiling is the
    # irreducible "recurring contact's occasional off-category spend" — which the
    # late-fusion layer (prior vs LLM disagreement → review) catches, not the floor.
    counterparty_min_observations: int = 2
    # The shrunk P(category | counterparty) must reach this for the prior to count
    # as a dominant, established signal.
    counterparty_dominance_floor: float = 0.65
    # Master switch for the counterparty recurrence prior in the rag_prompted stage.
    counterparty_prior_enabled: bool = True
    # Context window for annotation LLM calls. The few-shot prompt (system + 5
    # examples + full category list + txn) can exceed 2048 tokens, and Ollama
    # truncates silently from the front (the system prompt) — prompt_eval_count is
    # logged per call so truncation is observable.
    ollama_num_ctx: int = 2048
    # Replace verbalized LLM confidence with token-logprob mass on the category
    # value (requires an Ollama version that returns logprobs; falls back to the
    # verbalized number when logprobs are absent). Adopted 2026-07-02: -9.5%
    # Brier at identical labels on the golden-set eval.
    llm_logprob_confidence: bool = True
    # --- Learned merchant memory (stage 1.5) ---
    # A counterparty is promoted to a deterministic rule once it has
    # >= learned_rule_min_support human-verified labels for its modal category at
    # >= learned_rule_purity purity. Computed on-demand from annotations (no
    # materialized table); rules demote automatically when a correction lowers
    # purity. Personal counterparties are handled by the stage-1 person rule and
    # never reach here; the purity bar blocks mixed-purpose names. Adopted
    # 2026-07-03: clean-DB eval (e7b_control vs e7b_learned) showed accuracy
    # neutral, Brier -0.0015, auto-accept +0.85pp, review -0.85pp, and 21/234
    # recurring merchants labeled deterministically with no embedding/LLM call
    # at 100% causal precision, no learned-rule-caused regressions.
    learned_rule_enabled: bool = True
    learned_rule_min_support: int = 3
    learned_rule_purity: float = 0.9
    learned_rule_confidence: float = 0.95
    # Similarity floor for "apply to similar": neighbours at or above this cosine
    # similarity (or sharing the same UPI counterparty identity) are offered for
    # bulk re-annotation after a human correction.
    apply_similar_floor: float = 0.9
    api_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    # The annotation pipeline always captures a per-annotation reasoning trace
    # (routing trail, neighbours, similarity math vs thresholds, donor vote,
    # embed text, few-shot prompt content, LLM telemetry, raw vs dampened
    # confidence) into annotations.reasoning. DEV_MODE only gates the surface:
    # when on, the API returns the trace and the UI shows the "Why this
    # annotation?" panel. Off by default — regular users never see it, but
    # flipping it on explains past decisions too.
    dev_mode: bool = False

    # Last-segment values in UPI descriptions that carry no meaningful note.
    # Extend this list as you encounter new noise patterns — no code changes needed.
    upi_noise_keywords: list[str] = [
        "UPI", "NEFT", "IMPS", "RTGS", "NA", "NO REMARKS", "N/A", "NONE", "-",
    ]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
