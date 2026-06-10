"""Read/write helpers for the transactions table and related listing filters."""
from __future__ import annotations

import sqlite3

from src.models.transaction import Statement, Transaction, TxnRow


def insert_statement(conn: sqlite3.Connection, statement: Statement) -> None:
    """Insert a statement row; silently skips if it already exists (idempotent)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO statements
            (id, bank_name, parser_version, statement_month, period_start, period_end, file_sha256)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (statement.id, statement.bank_name, statement.parser_version,
         statement.statement_month,
         statement.period_start.isoformat() if statement.period_start else None,
         statement.period_end.isoformat() if statement.period_end else None,
         statement.file_sha256),
    )


def insert_transaction(conn: sqlite3.Connection, txn: Transaction) -> None:
    """Insert a transaction row. txn.statement_id must be set before calling."""
    if txn.statement_id is None:
        raise ValueError("transaction.statement_id must be set before inserting")
    conn.execute(
        """
        INSERT INTO transactions
            (id, statement_id, txn_date, amount, debit_credit, raw_description, running_balance, upi_meta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            txn.id,
            txn.statement_id,
            txn.txn_date.isoformat(),
            txn.amount,
            txn.debit_credit,
            txn.raw_description,
            txn.running_balance,
            txn.upi_meta,
        ),
    )


def list_transactions(
    conn: sqlite3.Connection,
    statement_id: str | None = None,
    month: str | None = None,
    unannotated: bool = False,
    include_annotation: bool = False,
    after: str | None = None,
    limit: int | None = None,
) -> list[TxnRow]:
    """Return transactions as a list of dicts, with optional filters.

    include_annotation joins each row's annotation (same shape as the per-id
    endpoint: annotation_id, category, ...) so list pages need one round-trip.
    after/limit give keyset pagination: pass the last row's id to get the next
    page in (txn_date, id) order.
    """
    if include_annotation:
        query = """
            SELECT t.*, a.id AS annotation_id, a.merchant, a.category, a.subcategory,
                   a.tags, a.confidence, a.source, a.original_source, a.annotated_at
            FROM transactions t
            LEFT JOIN annotations a ON a.transaction_id = t.id
        """
    else:
        query = "SELECT t.* FROM transactions t"
    params: list = []
    conditions: list[str] = []

    if unannotated:
        if not include_annotation:
            query += " LEFT JOIN annotations a ON a.transaction_id = t.id"
        conditions.append("a.id IS NULL")

    if statement_id:
        conditions.append("t.statement_id = ?")
        params.append(statement_id)

    if month:
        conditions.append("strftime('%Y-%m', t.txn_date) = ?")
        params.append(month)

    if after:
        cursor = conn.execute(
            "SELECT txn_date, id FROM transactions WHERE id = ?", (after,)
        ).fetchone()
        if cursor is not None:
            conditions.append("(t.txn_date, t.id) > (?, ?)")
            params += [cursor["txn_date"], cursor["id"]]

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY t.txn_date, t.id"

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]
