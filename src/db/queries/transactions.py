"""Read/write helpers for the transactions table and related listing filters."""
from __future__ import annotations

import sqlite3

from src.models.transaction import Statement, Transaction


def insert_statement(conn: sqlite3.Connection, statement: Statement) -> None:
    """Insert a statement row; silently skips if it already exists (idempotent)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month, file_sha256)
        VALUES (?, ?, ?, ?, ?)
        """,
        (statement.id, statement.bank_name, statement.parser_version,
         statement.statement_month, statement.file_sha256),
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
) -> list[dict]:
    """Return transactions as a list of dicts, with optional filters."""
    query = "SELECT t.* FROM transactions t"
    params: list = []
    conditions: list[str] = []

    if unannotated:
        query += " LEFT JOIN annotations a ON a.transaction_id = t.id"
        conditions.append("a.id IS NULL")

    if statement_id:
        conditions.append("t.statement_id = ?")
        params.append(statement_id)

    if month:
        conditions.append("strftime('%Y-%m', t.txn_date) = ?")
        params.append(month)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY t.txn_date, t.id"

    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]
