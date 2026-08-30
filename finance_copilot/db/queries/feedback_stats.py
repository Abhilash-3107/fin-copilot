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


def category_accuracy(conn: sqlite3.Connection, min_reviews: int = 3) -> list[dict]:
    """Per-category review accuracy from human feedback, weakest first (4.19).

    Accuracy = confirmed / reviewed for a category, aggregated across all model
    sources (confirmed keeps the label, refined/corrected changed it). Only
    categories with >= min_reviews reviewed transactions are included, so a
    single correction can't read as "0% accurate". Each row carries its sample
    size so the number is honest about how much it's based on.
    """
    rows = conn.execute(
        """
        SELECT category,
               SUM(confirmed)                        AS confirmed,
               SUM(refined)                          AS refined,
               SUM(corrected)                        AS corrected,
               SUM(confirmed + refined + corrected)  AS reviewed
        FROM feedback_stats
        GROUP BY category
        HAVING reviewed >= ?
        """,
        (min_reviews,),
    ).fetchall()
    out = []
    for r in rows:
        reviewed = r["reviewed"]
        out.append({
            "category": r["category"],
            "confirmed": r["confirmed"],
            "changed": r["refined"] + r["corrected"],
            "reviewed": reviewed,
            "accuracy": round(r["confirmed"] / reviewed, 4) if reviewed else 0.0,
        })
    # Weakest (lowest accuracy) first; ties broken by larger sample (more certain).
    out.sort(key=lambda x: (x["accuracy"], -x["reviewed"]))
    return out
