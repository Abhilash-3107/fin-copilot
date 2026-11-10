"""Settings: database path, Ollama URL, confidence threshold, and related configuration."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings


def dev_checkout_root() -> Path | None:
    """The repo root when running from a source checkout, else None (installed copy)."""
    repo_root = Path(__file__).parent.parent
    return repo_root if (repo_root / "pyproject.toml").exists() else None


def dotenv_file() -> Path | None:
    """The .env file Settings reads, or None for an installed copy.

    A checkout reads the repo's own .env. An installed copy reads none: a .env
    in whatever folder the user launches from (often some unrelated project)
    must not silently swap the database or AI endpoint. Real environment
    variables and --data-dir still apply.
    """
    repo_root = dev_checkout_root()
    return repo_root / ".env" if repo_root is not None else None


def default_data_dir() -> Path:
    """Where the app keeps its database and downloaded artifacts.

    Resolution order:
    1. FC_DATA_DIR env var - explicit override, also set by the launcher's --data-dir.
    2. A dev checkout (pyproject.toml sits next to the package) keeps the
       repo-relative data/ dir so the dev workflow is unchanged.
    3. Installed copies use the platform app-data dir (~/Library/Application
       Support/FinanceCopilot on macOS, %LOCALAPPDATA%\\FinanceCopilot on
       Windows, ~/.local/share/FinanceCopilot on Linux).
    """
    env = os.getenv("FC_DATA_DIR")
    if env:
        return Path(env).expanduser()
    repo_root = dev_checkout_root()
    if repo_root is not None:
        return repo_root / "data"
    import platformdirs

    return Path(platformdirs.user_data_dir("FinanceCopilot", appauthor=False))


class Settings(BaseSettings):
    data_dir: str = Field(default_factory=lambda: str(default_data_dir()))
    db_path: str = Field(default_factory=lambda: str(default_data_dir() / "finance.db"))
    # --- BYOM: pluggable LLM provider ---
    # "ollama": native Ollama API (default; structured output + logprobs).
    # "openai": any OpenAI-compatible /chat/completions endpoint (LM Studio,
    #           vLLM, OpenRouter, OpenAI itself) via llm_base_url + llm_api_key.
    # "none":   AI disabled; the pipeline degrades to rules + RAG-direct and
    #           routes everything else to the review queue.
    # "local":  llama-cpp-python running a GGUF in-process (pip extra
    #           finance-copilot[local-llm]); model downloads on demand into the
    #           data dir. See finance_copilot/local_llm.py.
    llm_provider: str = "ollama"
    # Manifest key in local_llm.MODEL_MANIFEST selecting which GGUF to run.
    local_llm_model: str = "qwen3.5-4b-q4_k_m"
    llm_base_url: str = ""  # e.g. https://api.openai.com/v1 (openai provider only)
    llm_api_key: str = ""
    llm_model: str = ""  # openai-provider model name; ollama uses ollama_model
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:4b"
    confidence_threshold: float = 0.85
    # --- Embedding provider seam (phase 2: RAG out of the box) ---
    # "ollama": nomic-embed-text via the Ollama API (today's path).
    # "local":  in-process fastembed/onnxruntime; weights download on first use
    #           into <data_dir>/models/embeddings. No external service needed.
    # "none":   embedding-dependent features (RAG, apply-to-similar) quietly
    #           don't fire; the pipeline stays rules + learned rules.
    embedding_provider: str = "ollama"
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"
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
    # Shortest payee key that can be a bank's truncation of a longer name
    # (pipeline/counterparty.truncation_variants). Kotak cuts at 15, or 14 with an
    # IFSC prefix; 13 covers a cut that lands on a space.
    counterparty_truncated_min_len: int = 13
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
    # LLM call robustness (pipeline/llm.py). llm_timeout_s bounds how long one
    # request may wait for the provider's answer; a cold model load on a slow
    # CPU is the long case. A connection that can't even be opened fails in 5s.
    # Transient failures (connection refused/reset, timeouts, 429, 5xx) are
    # retried with jittered exponential backoff up to llm_max_attempts; anything
    # else (bad key, unknown model) fails at once. One transaction's whole LLM
    # call, retries and self-repair re-asks included, is capped at
    # llm_max_attempts * llm_timeout_s (llm.call_budget_s). Raising
    # llm_timeout_s for a slow machine is safe: the job reaper never touches a
    # job whose worker is alive (api/live_jobs.py).
    llm_timeout_s: float = 60.0
    llm_max_attempts: int = 3
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
    # Cap on how many annotated rows run_summary scans + JSON-decodes in one
    # request. The dev-mode insight aggregates are corpus-wide, but a full scan
    # with per-row trace decode is CPU-heavy at scale; beyond this we take the
    # most-recent N and flag the payload `truncated` so the UI can disclose it.
    # Generous for the single-user local corpus this targets.
    run_summary_max_rows: int = 20_000
    api_base_url: str = "http://localhost:8000"
    # Opt-in shared secret for deliberate LAN exposure (4.9). Empty (the default)
    # means no auth - correct for the loopback-only single-user case. When set,
    # every /api request except /api/health must carry it as `Authorization:
    # Bearer <token>` or `X-API-Token: <token>`; the SPA prompts once and stores
    # it locally. This is a coarse gate for "I bound to 0.0.0.0 on my LAN on
    # purpose", not a user-management system.
    api_token: str = ""
    log_level: str = "INFO"
    # The annotation pipeline always captures a per-annotation reasoning trace
    # (routing trail, neighbours, similarity math vs thresholds, donor vote,
    # embed text, few-shot prompt content, LLM telemetry, raw vs dampened
    # confidence) into annotations.reasoning. DEV_MODE only gates the surface:
    # when on, the API returns the trace and the UI shows the "Why this
    # annotation?" panel. Off by default — regular users never see it, but
    # flipping it on explains past decisions too.
    dev_mode: bool = False
    # Hosted-demo hardening (FC_DEMO_MODE=1): the API refuses statement uploads,
    # backups, delete-everything, and AI-config writes so the demo box is
    # structurally incapable of receiving real user data. Enforced in middleware,
    # not the UI - even a curl user gets the 403. Never set this locally.
    demo_mode: bool = Field(default=False, validation_alias=AliasChoices("FC_DEMO_MODE", "DEMO_MODE"))

    # Last-segment values in UPI descriptions that carry no meaningful note.
    # Extend this list as you encounter new noise patterns - no code changes needed.
    upi_noise_keywords: list[str] = [
        "UPI", "NEFT", "IMPS", "RTGS", "NA", "NO REMARKS", "N/A", "NONE", "-",
    ]

    model_config = {"env_file": dotenv_file(), "extra": "ignore"}

    @model_validator(mode="after")
    def _explicit_data_dir_wins(self) -> Settings:
        # FC_DATA_DIR (what --data-dir sets) is the most explicit choice the user
        # can make, so it owns both paths. Otherwise a DB_PATH in whatever .env
        # sits in the cwd would quietly point `--data-dir X` at another database.
        env = os.getenv("FC_DATA_DIR")
        if env:
            data_dir = Path(env).expanduser()
            self.data_dir = str(data_dir)
            self.db_path = str(data_dir / "finance.db")
        return self


settings = Settings()


# Vector dimension per embedding model. A vec0 virtual table is fixed-dim, so a
# model with a different dimension forces a table rebuild + full re-embed (see
# ensure_vec_dimension). Single vector space invariant: one model at a time.
EMBEDDING_DIMS = {
    "nomic-embed-text": 768,
    "BAAI/bge-small-en-v1.5": 384,
}
DEFAULT_EMBEDDING_DIM = 768


def current_embedding_model() -> str:
    """The model identity stamped into embedding_meta and used as the retrieval
    compatibility filter - vectors from different models never mix."""
    if settings.embedding_provider == "local":
        return settings.local_embedding_model
    return settings.ollama_embedding_model


def current_embedding_dim() -> int:
    return EMBEDDING_DIMS.get(current_embedding_model(), DEFAULT_EMBEDDING_DIM)
