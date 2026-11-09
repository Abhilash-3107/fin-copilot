"""Read/write helpers for annotations, including the low-confidence review queue."""
from __future__ import annotations

import sqlite3

from finance_copilot.db.queries.categories import resolve_category_ids
from finance_copilot.models.annotation import Annotation
from finance_copilot.pipeline.sources import REVIEWABLE_SOURCES


def insert_annotation(conn: sqlite3.Connection, annotation: Annotation) -> bool:
    """Insert an annotation row, resolving category/subcategory ids.

    Inserts only when the transaction has no annotation yet: on a conflicting
    transaction_id it does nothing rather than overwriting, so a re-run or an
    errant POST can never clobber an existing row's original_source /
    annotated_at / reasoning history. Edits must go through update_annotation.
    Returns True when a row was inserted, False when one already existed.

    Unresolvable names (e.g. LLM free-text subcategories) leave the id NULL —
    callers that need strictness validate before inserting.
    """
    category_id, subcategory_id = resolve_category_ids(
        conn, annotation.category, annotation.subcategory
    )
    cur = conn.execute(
        """
        INSERT INTO annotations
            (id, transaction_id, merchant, category, subcategory, tags, confidence, source,
             category_id, subcategory_id, reasoning)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(transaction_id) DO NOTHING
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
    return cur.rowcount > 0


# Columns a patch is allowed to set. The column names are interpolated into the
# UPDATE (SQLite can't parameterize identifiers), so they must never come from
# request-shaped data — validate against this allowlist to keep the injection
# surface closed regardless of caller.
def replace_machine_annotation(
    conn: sqlite3.Connection,
    annotation_id: str,
    *,
    merchant: str | None,
    category: str,
    subcategory: str | None,
    tags: str | None,
    confidence: float,
    source: str,
    reasoning: str | None,
) -> bool:
    """Overwrite a machine-produced annotation with a re-evaluated one.

    The WHERE clause refuses any non-machine row, so a human label can never be
    replaced even if a caller gets the candidate set wrong. A snooze survives
    when the category is unchanged (the user already skipped this exact
    question) and clears when the answer changed. Caller commits.
    """
    from finance_copilot.pipeline.sources import MACHINE_SOURCES

    category_id, subcategory_id = resolve_category_ids(conn, category, subcategory)
    placeholders = ",".join("?" * len(MACHINE_SOURCES))
    cur = conn.execute(
        f"""
        UPDATE annotations
        SET review_snoozed_at = CASE WHEN category = ? THEN review_snoozed_at ELSE NULL END,
            merchant = ?, category = ?, subcategory = ?, tags = ?, confidence = ?,
            source = ?, reasoning = ?, category_id = ?, subcategory_id = ?,
            annotated_at = datetime('now')
        WHERE id = ? AND source IN ({placeholders})
        """,
        (category, merchant, category, subcategory, tags, confidence, source, reasoning,
         category_id, subcategory_id, annotation_id, *sorted(MACHINE_SOURCES)),
    )
    return cur.rowcount == 1


def demote_machine_annotation(conn: sqlite3.Connection, annotation_id: str, cap: float) -> bool:
    """Cap a machine annotation's confidence so it re-enters the review queue.
    Never touches human rows. Caller commits."""
    from finance_copilot.pipeline.sources import MACHINE_SOURCES

    placeholders = ",".join("?" * len(MACHINE_SOURCES))
    cur = conn.execute(
        f"UPDATE annotations SET confidence = MIN(confidence, ?) "
        f"WHERE id = ? AND confidence > ? AND source IN ({placeholders})",
        (cap, annotation_id, cap, *sorted(MACHINE_SOURCES)),
    )
    return cur.rowcount == 1


_UPDATABLE_COLUMNS = frozenset(
    {"merchant", "category", "subcategory", "tags", "confidence", "category_id", "subcategory_id"}
)


def update_annotation(conn: sqlite3.Connection, annotation_id: str, patch: dict) -> None:
    """Update an existing annotation, including explicit None values (clears the field).

    Sets source='manual', preserving the pipeline source in original_source on
    the first manual touch, and refreshes annotated_at.
    """
    unknown = set(patch) - _UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"update_annotation: disallowed column(s) {sorted(unknown)}")

    set_clauses = [f"{col} = ?" for col in patch]
    values = list(patch.values())
    set_clauses += [
        "original_source = COALESCE(original_source, source)",
        "source = 'manual'",
        "annotated_at = datetime('now')",
        # A correction is engagement; drop any lingering snooze so the flag never
        # outlives the reason it was set.
        "review_snoozed_at = NULL",
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
    """Return model annotations below the confidence threshold, joined with their transactions.

    Snoozed rows (skipped by the user) are excluded so a skip sticks across
    sessions instead of silently reappearing; see snooze_annotation / count_snoozed.
    """
    sources = sorted(REVIEWABLE_SOURCES)
    placeholders = ",".join("?" for _ in sources)
    rows = conn.execute(
        f"""
        SELECT a.*, a.id AS annotation_id, t.txn_date, t.amount, t.debit_credit, t.raw_description
        FROM annotations a
        JOIN transactions t ON t.id = a.transaction_id
        WHERE a.source IN ({placeholders}) AND a.confidence < ?
          AND a.review_snoozed_at IS NULL
        -- Uncertainty × impact ordering: a wrong label on a large transaction
        -- costs more than one on a small one, so weight by log-amount instead of
        -- ranking purely by confidence.
        ORDER BY (1.0 - a.confidence) * ln(1.0 + abs(t.amount)) DESC, a.annotated_at ASC
        """,
        (*sources, threshold),
    ).fetchall()
    return [dict(row) for row in rows]


def snooze_annotation(conn: sqlite3.Connection, annotation_id: str) -> bool:
    """Snooze a low-confidence annotation out of the review queue (a "skip" that
    sticks). Idempotent; returns True when a row was updated. Caller commits."""
    cur = conn.execute(
        "UPDATE annotations SET review_snoozed_at = datetime('now') WHERE id = ?",
        (annotation_id,),
    )
    return cur.rowcount > 0


def unsnooze_all(conn: sqlite3.Connection) -> int:
    """Clear every snooze so skipped items return to the queue. Returns the count
    cleared. Caller commits."""
    cur = conn.execute(
        "UPDATE annotations SET review_snoozed_at = NULL WHERE review_snoozed_at IS NOT NULL"
    )
    return cur.rowcount


def count_snoozed(conn: sqlite3.Connection, threshold: float) -> int:
    """How many below-threshold model annotations are currently snoozed - the
    count the queue surfaces so snoozed items are never fully hidden."""
    sources = sorted(REVIEWABLE_SOURCES)
    placeholders = ",".join("?" for _ in sources)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM annotations
        WHERE source IN ({placeholders}) AND confidence < ?
          AND review_snoozed_at IS NOT NULL
        """,
        (*sources, threshold),
    ).fetchone()
    return row["n"] if row else 0
