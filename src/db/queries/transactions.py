"""Read/write helpers for the transactions table and related listing filters."""
from __future__ import annotations

import sqlite3

from src.models.transaction import Statement, Transaction, TxnRow


def insert_statement(conn: sqlite3.Connection, statement: Statement) -> None:
    """Insert a statement row; silently skips if it already exists (idempotent)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO statements
            (id, bank_name, parser_version, statement_month, period_start, period_end,
             file_sha256, opening_balance, closing_balance, account_ref)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (statement.id, statement.bank_name, statement.parser_version,
         statement.statement_month,
         statement.period_start.isoformat() if statement.period_start else None,
         statement.period_end.isoformat() if statement.period_end else None,
         statement.file_sha256, statement.opening_balance, statement.closing_balance,
         statement.account_ref),
    )


def insert_transaction(conn: sqlite3.Connection, txn: Transaction) -> None:
    """Insert a transaction row. txn.statement_id must be set before calling."""
    if txn.statement_id is None:
        raise ValueError("transaction.statement_id must be set before inserting")
    from src.pipeline.counterparty import normalize_identity

    conn.execute(
        """
        INSERT INTO transactions
            (id, statement_id, txn_date, amount, debit_credit, raw_description, running_balance, upi_meta, counterparty_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            normalize_identity(txn.raw_description),
        ),
    )


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so user input matches literally (ESCAPE '\\')."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_transactions(
    conn: sqlite3.Connection,
    statement_id: str | None = None,
    month: str | None = None,
    unannotated: bool = False,
    include_annotation: bool = False,
    q: str | None = None,
    categories: list[str] | None = None,
    sources: list[str] | None = None,
    merchant: str | None = None,
    after: str | None = None,
    limit: int | None = None,
) -> list[TxnRow]:
    """Return transactions as a list of dicts, with optional filters.

    include_annotation joins each row's annotation (same shape as the per-id
    endpoint: annotation_id, category, ...) so list pages need one round-trip.
    q is a case-insensitive substring match over the raw description, the
    annotated merchant, and the UPI note. categories/sources/merchant filter
    on annotation fields, so they only ever match annotated rows.
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

    needs_annotation_join = unannotated or q or categories or sources or merchant
    if needs_annotation_join and not include_annotation:
        query += " LEFT JOIN annotations a ON a.transaction_id = t.id"

    if unannotated:
        conditions.append("a.id IS NULL")

    if q:
        pattern = f"%{_escape_like(q.lower())}%"
        conditions.append(
            """(
                LOWER(t.raw_description) LIKE ? ESCAPE '\\'
                OR LOWER(a.merchant) LIKE ? ESCAPE '\\'
                OR (json_valid(t.upi_meta)
                    AND LOWER(json_extract(t.upi_meta, '$.note')) LIKE ? ESCAPE '\\')
            )"""
        )
        params += [pattern, pattern, pattern]

    if categories:
        placeholders = ",".join("?" * len(categories))
        conditions.append(f"a.category IN ({placeholders})")
        params += categories

    if sources:
        placeholders = ",".join("?" * len(sources))
        conditions.append(f"a.source IN ({placeholders})")
        params += sources

    if merchant:
        conditions.append("a.merchant = ?")
        params.append(merchant)

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


def list_transaction_facets(
    conn: sqlite3.Connection,
    statement_id: str | None = None,
    month: str | None = None,
) -> dict[str, list[str]]:
    """Distinct annotation categories and sources within the statement/month
    scope, for populating filter options. Deliberately ignores the annotation
    filters themselves so an active filter never hides its sibling options."""
    conditions: list[str] = []
    params: list = []
    if statement_id:
        conditions.append("t.statement_id = ?")
        params.append(statement_id)
    if month:
        conditions.append("strftime('%Y-%m', t.txn_date) = ?")
        params.append(month)
    where = (" AND " + " AND ".join(conditions)) if conditions else ""

    def distinct(column: str) -> list[str]:
        rows = conn.execute(
            f"""
            SELECT DISTINCT a.{column} AS v
            FROM annotations a
            JOIN transactions t ON t.id = a.transaction_id
            WHERE a.{column} IS NOT NULL{where}
            ORDER BY v
            """,
            params,
        ).fetchall()
        return [row["v"] for row in rows]

    return {"categories": distinct("category"), "sources": distinct("source")}
