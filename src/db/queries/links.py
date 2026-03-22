"""CRUD helpers for transaction_links."""
from __future__ import annotations

import sqlite3

import ulid


def insert_link(
    conn: sqlite3.Connection,
    txn_a: str,
    txn_b: str,
    link_type: str,
    note: str | None = None,
) -> dict:
    """Insert a link between two transactions. Canonical ordering (txn_a < txn_b) is enforced."""
    a, b = (txn_a, txn_b) if txn_a < txn_b else (txn_b, txn_a)
    link_id = str(ulid.ULID())
    conn.execute(
        """
        INSERT INTO transaction_links (id, txn_a, txn_b, link_type, note)
        VALUES (?, ?, ?, ?, ?)
        """,
        (link_id, a, b, link_type, note),
    )
    return {"id": link_id, "txn_a": a, "txn_b": b, "link_type": link_type, "note": note}


def delete_link(conn: sqlite3.Connection, link_id: str) -> bool:
    """Delete a link by id. Returns True if a row was deleted."""
    cursor = conn.execute("DELETE FROM transaction_links WHERE id = ?", (link_id,))
    return cursor.rowcount > 0


def list_links_for_transaction(conn: sqlite3.Connection, transaction_id: str) -> list[dict]:
    """Return all links involving a transaction, with the other transaction's basic fields."""
    rows = conn.execute(
        """
        SELECT
            l.id, l.link_type, l.note, l.created_at,
            l.txn_a, l.txn_b,
            CASE WHEN l.txn_a = ? THEN l.txn_b ELSE l.txn_a END AS other_txn_id,
            t.txn_date AS other_date,
            t.amount AS other_amount,
            t.debit_credit AS other_debit_credit,
            t.raw_description AS other_description
        FROM transaction_links l
        JOIN transactions t ON t.id = CASE WHEN l.txn_a = ? THEN l.txn_b ELSE l.txn_a END
        WHERE l.txn_a = ? OR l.txn_b = ?
        ORDER BY l.created_at
        """,
        (transaction_id, transaction_id, transaction_id, transaction_id),
    ).fetchall()
    return [dict(row) for row in rows]
