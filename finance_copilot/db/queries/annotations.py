"""Read/write helpers for annotations, including the low-confidence review queue."""
from __future__ import annotations

import sqlite3

from finance_copilot.db.queries.categories import resolve_category_ids
from finance_copilot.db.queries.common import UNCATEGORIZED, unsorted_sql
from finance_copilot.db.queries.transfers import unpair_if_relabelled
from finance_copilot.models.annotation import Annotation
from finance_copilot.pipeline.sources import REVIEWABLE_SOURCES, VERIFIED_SOURCES


def _resolve_label(conn: sqlite3.Connection, category: str, subcategory: str | None) -> tuple:
    """(category_id, subcategory, subcategory_id) for a machine-written label."""
    from finance_copilot.db.queries.taxonomy import canonical_subcategory

    category_id, subcategory_id = resolve_category_ids(conn, category, subcategory)
    if subcategory and subcategory_id is None:
        subcategory, subcategory_id = canonical_subcategory(conn, category_id, subcategory)
    return category_id, subcategory, subcategory_id


def insert_annotation(conn: sqlite3.Connection, annotation: Annotation) -> bool:
    """Insert an annotation row, resolving category/subcategory ids.

    Inserts only when the transaction has no annotation yet: on a conflicting
    transaction_id it does nothing rather than overwriting, so a re-run or an
    errant POST can never clobber an existing row's original_source /
    annotated_at / reasoning history. Edits must go through update_annotation.
    Returns True when a row was inserted, False when one already existed. A new
    annotation also clears the payment's snooze: a skipped payment that now has
    an answer (a hand label, or a machine guess from a later run) is a new
    question, not the one that was set aside.

    A subcategory name outside the taxonomy (an LLM's free text, a learned
    rule's stale name) is snapped to the sibling it plainly means or dropped,
    so writes never create orphaned subcategories. Callers that need
    strictness (a user's choice) validate before inserting.
    """
    category_id, subcategory, subcategory_id = _resolve_label(conn, annotation.category, annotation.subcategory)
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
            subcategory,
            annotation.tags,
            annotation.confidence,
            annotation.source,
            category_id,
            subcategory_id,
            annotation.reasoning,
        ),
    )
    if cur.rowcount > 0:
        conn.execute(
            "UPDATE transactions SET review_snoozed_at = NULL WHERE id = ?", (annotation.transaction_id,)
        )
    return cur.rowcount > 0


# Columns a patch is allowed to set. The column names are interpolated into the
# UPDATE (SQLite can't parameterize identifiers), so they must never come from
# request-shaped data - validate against this allowlist to keep the injection
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
    replaced even if a caller gets the candidate set wrong. The payment's snooze
    survives when the category is unchanged (the user already skipped this exact
    question) and clears when the answer changed. Caller commits.
    """
    from finance_copilot.pipeline.sources import MACHINE_SOURCES

    category_id, subcategory, subcategory_id = _resolve_label(conn, category, subcategory)
    placeholders = ",".join("?" * len(MACHINE_SOURCES))
    previous = conn.execute(
        f"SELECT transaction_id, category FROM annotations WHERE id = ? AND source IN ({placeholders})",
        (annotation_id, *sorted(MACHINE_SOURCES)),
    ).fetchone()
    if previous is None:
        return False
    conn.execute(
        """
        UPDATE annotations
        SET merchant = ?, category = ?, subcategory = ?, tags = ?, confidence = ?,
            source = ?, reasoning = ?, category_id = ?, subcategory_id = ?,
            annotated_at = datetime('now')
        WHERE id = ?
        """,
        (merchant, category, subcategory, tags, confidence, source, reasoning,
         category_id, subcategory_id, annotation_id),
    )
    if previous["category"] != category:
        conn.execute(
            "UPDATE transactions SET review_snoozed_at = NULL WHERE id = ?", (previous["transaction_id"],)
        )
    return True


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
    if patch.get("category") == UNCATEGORIZED:
        # Uncategorized has no subcategories; a stale one would label the row
        # with a category it does not belong to.
        patch = {**patch, "subcategory": None, "subcategory_id": None}

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
    # A category other than Self Transfers on a payment that is in a transfer says it
    # is not one: the pair goes, whoever labelled this side before (this side is the
    # user's now, source manual).
    target = conn.execute("SELECT transaction_id FROM annotations WHERE id = ?", (annotation_id,)).fetchone()
    if target is not None:
        unpair_if_relabelled(conn, target["transaction_id"], patch.get("category"))
    # A correction is engagement; drop any lingering snooze so the flag never
    # outlives the reason it was set.
    conn.execute(
        "UPDATE transactions SET review_snoozed_at = NULL "
        "WHERE id = (SELECT transaction_id FROM annotations WHERE id = ?)",
        (annotation_id,),
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


def _queue_condition(threshold: float) -> tuple[str, tuple]:
    """The one definition of "belongs in the review queue", over transactions t
    LEFT JOINed to annotations a. Two kinds of payment, in either direction:
    a machine label below the confidence bar (a guess to check), and anything
    not sorted by common.unsorted_sql (no annotation, or a NULL or Uncategorized
    category) whatever its source and confidence. So the queue is a superset of
    what the Dashboard and Money Map call not sorted, and a payment counted as
    unsorted can always be reached here. A label the user made or wrote a rule
    for is never a guess to check, whatever its confidence; only an
    Uncategorized one is still queued."""
    guessers = sorted(REVIEWABLE_SOURCES - VERIFIED_SOURCES - {"user_rule"})
    placeholders = ",".join("?" for _ in guessers)
    sql = f"((a.source IN ({placeholders}) AND a.confidence < ?) OR {unsorted_sql('a')})"
    return sql, (*guessers, threshold)


def list_review_queue(conn: sqlite3.Connection, threshold: float) -> list[dict]:
    """Payments that still need a human answer, keyed by transaction, most
    worth asking about first.

    Items without an annotation carry annotation_id/category/source None and
    confidence 0. Snoozed payments (skipped by the user) are excluded so a skip
    sticks across sessions; see snooze_transaction / count_snoozed.
    """
    condition, params = _queue_condition(threshold)
    rows = conn.execute(
        f"""
        SELECT t.id AS transaction_id, t.txn_date, t.amount, t.debit_credit, t.raw_description,
               t.statement_id, t.counterparty_key, t.display_name, t.user_note, t.upi_meta,
               a.id AS annotation_id, a.merchant, a.category, a.subcategory, a.tags,
               COALESCE(a.confidence, 0.0) AS confidence, a.source, a.original_source,
               a.annotated_at, a.reasoning
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE {condition} AND t.review_snoozed_at IS NULL
        -- Uncertainty x impact ordering: a wrong label on a large transaction
        -- costs more than one on a small one, so weight by log-amount instead of
        -- ranking purely by confidence.
        ORDER BY (1.0 - COALESCE(a.confidence, 0.0)) * ln(1.0 + abs(t.amount)) DESC, t.txn_date ASC, t.id
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def count_in_review_queue(conn: sqlite3.Connection, threshold: float, transaction_ids: list[str]) -> int:
    """How many of these payments the review queue would ask about, by the
    same condition list_review_queue uses (snoozing aside: a skip hides a
    payment from Teach Me, it does not make it sorted)."""
    condition, params = _queue_condition(threshold)
    total = 0
    for start in range(0, len(transaction_ids), 500):
        chunk = transaction_ids[start:start + 500]
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM transactions t
            LEFT JOIN annotations a ON a.transaction_id = t.id
            WHERE t.id IN ({','.join('?' for _ in chunk)}) AND {condition}
            """,
            (*chunk, *params),
        ).fetchone()
        total += row["n"]
    return total


def snooze_transaction(conn: sqlite3.Connection, transaction_id: str) -> bool:
    """Snooze a payment out of the review queue (a "skip" that sticks), whether
    or not it has an annotation. Idempotent; returns True when the payment
    exists. Caller commits."""
    cur = conn.execute(
        "UPDATE transactions SET review_snoozed_at = datetime('now') WHERE id = ?",
        (transaction_id,),
    )
    return cur.rowcount > 0


def queue_counts(conn: sqlite3.Connection, threshold: float) -> dict:
    """How many payments wait in the queue, without loading it: `unsorted` have
    no real category (no guess, or Uncategorized), `guesses` have a machine
    label below the bar, `set_aside` are skipped ones that would otherwise be
    queued (`set_aside_unsorted` of them have no real category). The first two
    are what Teach Me will ask; unsorted, guesses and set_aside never overlap."""
    condition, params = _queue_condition(threshold)
    row = conn.execute(
        f"""
        SELECT
          COALESCE(SUM(t.review_snoozed_at IS NULL AND {unsorted_sql('a')}), 0) AS unsorted,
          COALESCE(SUM(t.review_snoozed_at IS NULL AND NOT {unsorted_sql('a')}), 0) AS guesses,
          COALESCE(SUM(t.review_snoozed_at IS NOT NULL), 0) AS set_aside,
          COALESCE(SUM(t.review_snoozed_at IS NOT NULL AND {unsorted_sql('a')}), 0) AS set_aside_unsorted
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE {condition}
        """,
        params,
    ).fetchone()
    return {key: row[key] for key in ("unsorted", "guesses", "set_aside", "set_aside_unsorted")}


def sorted_counts_by_statement(conn: sqlite3.Connection, threshold: float) -> dict[str, int]:
    """Per statement, how many payments count as sorted: the ones Teach Me is
    not waiting on. The complement of _queue_condition, so "sorted" means the
    same thing here as on the review queue - a guess below the review bar and a
    payment with no real category are not sorted, and a skipped payment is
    exactly as sorted as it was before it was skipped."""
    condition, params = _queue_condition(threshold)
    rows = conn.execute(
        f"""
        SELECT t.statement_id, COALESCE(SUM(NOT {condition}), 0) AS n
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        GROUP BY t.statement_id
        """,
        params,
    ).fetchall()
    return {row["statement_id"]: row["n"] for row in rows}


def sorted_payment_coverage(conn: sqlite3.Connection, threshold: float, model_version: str) -> tuple[int, int]:
    """(ready, total) over sorted payments, by the same definition as
    sorted_counts_by_statement: total is every sorted payment, ready the ones
    with a vector from the active embedding model. Only sorted payments serve
    as examples for matching, so a payment still waiting in Teach Me is neither."""
    condition, params = _queue_condition(threshold)
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(NOT {condition}), 0) AS total,
               COALESCE(SUM(NOT {condition} AND em.id IS NOT NULL), 0) AS ready
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        LEFT JOIN embedding_meta em ON em.transaction_id = t.id AND em.model_version = ?
        """,
        (*params, *params, model_version),
    ).fetchone()
    return row["ready"], row["total"]


def count_snoozed(conn: sqlite3.Connection, threshold: float) -> int:
    """How many payments are set aside: snoozed and would otherwise be queued."""
    return queue_counts(conn, threshold)["set_aside"]


def unsnooze_all(conn: sqlite3.Connection, threshold: float) -> int:
    """Clear every snooze so skipped payments return to the queue. Returns how
    many came back (a snoozed payment that has since been answered is cleared
    too but not counted). Caller commits."""
    came_back = count_snoozed(conn, threshold)
    conn.execute("UPDATE transactions SET review_snoozed_at = NULL WHERE review_snoozed_at IS NOT NULL")
    return came_back
