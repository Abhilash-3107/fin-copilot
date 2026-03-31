"""Read/write helpers for annotations, including the low-confidence review queue."""
from __future__ import annotations

import sqlite3

from src.models.annotation import Annotation


def insert_annotation(conn: sqlite3.Connection, annotation: Annotation) -> None:
    """Insert or replace an annotation row."""
    conn.execute(
        """
        INSERT OR REPLACE INTO annotations
            (id, transaction_id, merchant, category, subcategory, tags, confidence, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            annotation.id,
            annotation.transaction_id,
            annotation.merchant,
            annotation.category,
            annotation.subcategory,
            annotation.tags,
            annotation.confidence,
            annotation.source,
        ),
    )


def update_annotation(conn: sqlite3.Connection, annotation_id: str, patch: dict) -> None:
    """Update an existing annotation. Always sets source='manual' and refreshes annotated_at."""
    patch = {k: v for k, v in patch.items() if v is not None}
    set_clauses = ", ".join(f"{col} = ?" for col in patch)
    values = list(patch.values())

    conn.execute(
        f"""
        UPDATE annotations
        SET {set_clauses}, source = 'manual', annotated_at = datetime('now')
        WHERE id = ?
        """,
        [*values, annotation_id],
    )


def get_annotation(conn: sqlite3.Connection, annotation_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM annotations WHERE id = ?", (annotation_id,)
    ).fetchone()
    return dict(row) if row else None


def get_annotation_by_transaction(conn: sqlite3.Connection, transaction_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM annotations WHERE transaction_id = ?", (transaction_id,)
    ).fetchone()
    return dict(row) if row else None


def list_review_queue(conn: sqlite3.Connection, threshold: float) -> list[dict]:
    """Return model annotations below the confidence threshold, joined with their transactions."""
    rows = conn.execute(
        """
        SELECT a.*, t.txn_date, t.amount, t.debit_credit, t.raw_description
        FROM annotations a
        JOIN transactions t ON t.id = a.transaction_id
        WHERE a.source IN ('model','rule','rag_direct','rag_prompted','llm') AND a.confidence < ?
        ORDER BY a.confidence ASC, a.annotated_at ASC
        """,
        (threshold,),
    ).fetchall()
    return [dict(row) for row in rows]
