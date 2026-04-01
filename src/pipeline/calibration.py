"""Bayesian confidence calibration based on human feedback history.

Replaces the flat llm_confidence_dampen / llm_confidence_dampen_rag multipliers
with a per-(source, category) dynamic factor that improves as humans confirm or
correct annotations.

Beta distribution prior is derived from the static settings values so that with
zero feedback, behavior is identical to the original pipeline.
"""
from __future__ import annotations

import sqlite3

from src.config import settings
from src.db.queries.feedback_stats import get_feedback_stats


def get_calibrated_dampening(
    conn: sqlite3.Connection,
    source: str,
    category: str,
) -> float:
    """Return the dampening factor to apply to an LLM confidence score.

    For sources that are not dampened (rules, rag_direct), returns 1.0.

    For "llm" and "rag_prompted", starts from a Beta prior derived from the
    static settings values and updates it with accumulated human feedback:

      alpha = prior_alpha + confirmed + 0.5 * refined
      beta  = prior_beta  + corrected
      dampening = alpha / (alpha + beta)

    With no feedback: returns exactly the static setting (0.85 or 0.92).
    As confirmations accumulate: dampening rises toward 1.0.
    As corrections accumulate: dampening falls.
    """
    if source == "llm":
        base = settings.llm_confidence_dampen
    elif source == "rag_prompted":
        base = settings.llm_confidence_dampen_rag
    else:
        return 1.0

    # Derive prior from the static setting so changing the config shifts the prior.
    # 5 pseudo-observations: responsive enough that ~5 confirmations can lift a category
    # above threshold, but heavy enough that a single event doesn't swing the score.
    prior_weight = 5.0
    prior_alpha = base * prior_weight
    prior_beta = (1.0 - base) * prior_weight

    row = get_feedback_stats(conn, source, category)
    if row is None:
        return prior_alpha / (prior_alpha + prior_beta)  # == base exactly

    alpha = prior_alpha + row["confirmed"] + 0.5 * row["refined"]
    beta = prior_beta + row["corrected"]
    return alpha / (alpha + beta)
