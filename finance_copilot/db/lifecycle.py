"""Bring a freshly opened ledger in line with the running process.

Runs at app startup and again after an in-app restore swaps in a different
database, so both paths reconcile identically.
"""
from __future__ import annotations

import logging
import sqlite3

from finance_copilot.db.connection import init_db

logger = logging.getLogger(__name__)


def reconcile_database(conn: sqlite3.Connection, *, reset_llm_settings: bool = False) -> None:
    """Migrate, apply stored model config, fit the vector table, reap dead jobs.

    reset_llm_settings first returns the in-memory model config to its
    env/defaults, so a restored ledger's settings fully replace the old ones
    instead of layering over whatever the previous ledger had set.
    """
    from finance_copilot.api.routes.annotations import reap_interrupted_jobs
    from finance_copilot.config import Settings, settings
    from finance_copilot.db.queries.app_settings import (
        _LLM_KEYS,
        apply_llm_config,
        ensure_llm_provider_default,
    )
    from finance_copilot.db.queries.embeddings import ensure_vec_dimension

    init_db(conn)
    if reset_llm_settings:
        defaults = Settings()
        for key in _LLM_KEYS:
            setattr(settings, key, getattr(defaults, key))
    # First run only: pick "ollama" vs "none" from a one-shot probe so a machine
    # without Ollama starts in the honest no-AI tier instead of timing out per
    # request.
    ensure_llm_provider_default(conn)
    conn.commit()
    # Stored BYOM/LLM overrides onto the live settings, so the pipeline uses the
    # user's provider choice from the next request.
    apply_llm_config(conn)
    # The vector table must match the active embedding model's dimension.
    if ensure_vec_dimension(conn):
        logger.warning(
            "embedding dimension changed - vector memory cleared; run a re-embed "
            "from Settings > AI & Model to rebuild RAG retrieval"
        )
    # Jobs still marked in-flight were orphaned by a previous process (or frozen
    # into a restored snapshot); fail them or they block the single-job guard.
    reaped = reap_interrupted_jobs(conn)
    if reaped:
        logger.warning("failed %d annotation job(s) orphaned by the previous process", reaped)
