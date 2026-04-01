"""Read/write helpers for the feedback_stats table used in Bayesian confidence calibration."""
from __future__ import annotations

import sqlite3

from typing import Literal

FeedbackType = Literal["confirmed", "refined", "corrected"]


def record_feedback(
    conn: sqlite3.Connection,
    source: str,
    category: str,
    feedback_type: FeedbackType,
) -> None:
    """Upsert a feedback event for (source, category).

    feedback_type:
      - "confirmed": human reviewed annotation and it was correct as-is
      - "refined":   human kept the category but changed subcategory/merchant/tags
      - "corrected": human changed the category (wrong prediction)
    """
    confirmed = 1 if feedback_type == "confirmed" else 0
    refined = 1 if feedback_type == "refined" else 0
    corrected = 1 if feedback_type == "corrected" else 0

    conn.execute(
        """
        INSERT INTO feedback_stats (source, category, confirmed, refined, corrected)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source, category) DO UPDATE SET
            confirmed  = confirmed  + excluded.confirmed,
            refined    = refined    + excluded.refined,
            corrected  = corrected  + excluded.corrected,
            updated_at = datetime('now')
        """,
        (source, category, confirmed, refined, corrected),
    )


def get_feedback_stats(
    conn: sqlite3.Connection,
    source: str,
    category: str,
) -> dict | None:
    """Return the feedback_stats row for (source, category), or None if no data yet."""
    row = conn.execute(
        "SELECT * FROM feedback_stats WHERE source = ? AND category = ?",
        (source, category),
    ).fetchone()
    return dict(row) if row else None
