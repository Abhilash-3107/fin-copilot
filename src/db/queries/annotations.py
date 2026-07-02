"""Read/write helpers for annotations, including the low-confidence review queue."""
from __future__ import annotations

import sqlite3

from src.db.queries.categories import resolve_category_ids
from src.models.annotation import Annotation


def insert_annotation(conn: sqlite3.Connection, annotation: Annotation) -> None:
    """Insert or replace an annotation row, resolving category/subcategory ids.

    Unresolvable names (e.g. LLM free-text subcategories) leave the id NULL —
    callers that need strictness validate before inserting.
    """
    category_id, subcategory_id = resolve_category_ids(
        conn, annotation.category, annotation.subcategory
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO annotations
            (id, transaction_id, merchant, category, subcategory, tags, confidence, source,
             category_id, subcategory_id, reasoning)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            category_id,
            subcategory_id,
            annotation.reasoning,
        ),
    )


def update_annotation(conn: sqlite3.Connection, annotation_id: str, patch: dict) -> None:
    """Update an existing annotation, including explicit None values (clears the field).

    Sets source='manual', preserving the pipeline source in original_source on
    the first manual touch, and refreshes annotated_at.
    """
    set_clauses = [f"{col} = ?" for col in patch]
    values = list(patch.values())
    set_clauses += [
        "original_source = COALESCE(original_source, source)",
        "source = 'manual'",
        "annotated_at = datetime('now')",
    ]

    conn.execute(
        f"UPDATE annotations SET {', '.join(set_clauses)} WHERE id = ?",
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
        SELECT a.*, a.id AS annotation_id, t.txn_date, t.amount, t.debit_credit, t.raw_description
        FROM annotations a
        JOIN transactions t ON t.id = a.transaction_id
        WHERE a.source IN ('model','rule','rag_direct','rag_knn','rag_prompted','llm') AND a.confidence < ?
        -- Uncertainty × impact ordering: a wrong label on a large transaction
        -- costs more than one on a small one, so weight by log-amount instead of
        -- ranking purely by confidence.
        ORDER BY (1.0 - a.confidence) * ln(1.0 + abs(t.amount)) DESC, a.annotated_at ASC
        """,
        (threshold,),
    ).fetchall()
    return [dict(row) for row in rows]
