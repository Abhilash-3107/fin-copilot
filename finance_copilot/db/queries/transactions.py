"""Read/write helpers for the transactions table and related listing filters."""
from __future__ import annotations

import sqlite3

from finance_copilot.db.queries.common import UNCATEGORIZED, unsorted_sql
from finance_copilot.models.transaction import Statement, Transaction, TxnRow


def insert_statement(conn: sqlite3.Connection, statement: Statement) -> None:
    """Insert a statement row; silently skips if it already exists (idempotent)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO statements
            (id, bank_name, parser_version, statement_month, period_start, period_end,
             file_sha256, opening_balance, closing_balance, account_ref,
             account_id, printed_start, printed_end)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (statement.id, statement.bank_name, statement.parser_version,
         statement.statement_month,
         statement.period_start.isoformat() if statement.period_start else None,
         statement.period_end.isoformat() if statement.period_end else None,
         statement.file_sha256, statement.opening_balance, statement.closing_balance,
         statement.account_ref, statement.account_id,
         statement.printed_start.isoformat() if statement.printed_start else None,
         statement.printed_end.isoformat() if statement.printed_end else None),
    )


def statement_balance_checks(
    conn: sqlite3.Connection, tolerance: float = 0.01, statement_id: str | None = None
) -> dict[str, dict]:
    """Per-statement running-balance reconciliation, keyed by statement_id.

    For each consecutive pair of balance-carrying rows (in parse order), checks
    that the balance delta matches the signed amount between them - the same
    rigor the parser used to pick the layout, recomputed from the stored
    running_balance so the UI can show "N/N rows balanced" and flag any break.

    Returns {statement_id: {"checked": int, "consistent": int, "balanced": bool}}.
    A statement with no balance chain (checked == 0) is reported balanced=True
    (nothing to contradict), so the UI shows a check only when checked > 0.
    With statement_id, only that statement is computed.
    """
    rows = conn.execute(
        """
        WITH chain AS (
            SELECT
                statement_id,
                running_balance,
                amount,
                debit_credit,
                LAG(running_balance) OVER (
                    PARTITION BY statement_id ORDER BY rowid
                ) AS prev_balance
            FROM transactions
            WHERE running_balance IS NOT NULL AND (? IS NULL OR statement_id = ?)
        )
        SELECT
            statement_id,
            COUNT(*) AS checked,
            SUM(
                CASE WHEN ABS(
                    (running_balance - prev_balance)
                    - (CASE WHEN debit_credit = 'credit' THEN amount ELSE -amount END)
                ) <= ? THEN 1 ELSE 0 END
            ) AS consistent
        FROM chain
        WHERE prev_balance IS NOT NULL AND debit_credit IS NOT NULL
        GROUP BY statement_id
        """,
        (statement_id, statement_id, tolerance),
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        checked = r["checked"] or 0
        consistent = r["consistent"] or 0
        out[r["statement_id"]] = {
            "checked": checked,
            "consistent": consistent,
            "balanced": consistent == checked,
        }
    return out


def insert_transaction(conn: sqlite3.Connection, txn: Transaction) -> None:
    """Insert a transaction row. txn.statement_id must be set before calling."""
    if txn.statement_id is None:
        raise ValueError("transaction.statement_id must be set before inserting")
    from finance_copilot.pipeline.counterparty import normalize_identity, resolve_counterparty_key

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
            resolve_counterparty_key(conn, normalize_identity(txn.raw_description)),
        ),
    )


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so user input matches literally (ESCAPE '\\')."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_amount_query(q: str) -> float | None:
    """Parse a search query as a rupee amount, tolerating ₹, commas, and spaces.

    Returns the float when the whole query is numeric (so 'swiggy' isn't treated
    as an amount), else None. Powers amount search from the global palette.
    """
    cleaned = q.strip().lstrip("₹").replace(",", "").replace(" ", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _filter_conditions(
    conn: sqlite3.Connection,
    *,
    statement_id: str | None,
    month: str | None,
    unannotated: bool,
    q: str | None,
    categories: list[str] | None,
    sources: list[str] | None,
    merchant: str | None,
    payee_keys: list[str] | None,
    person_token: str | None,
    min_amount: float | None,
    max_amount: float | None,
) -> tuple[list[str], list]:
    """WHERE conditions and params shared by the list and its summary, over
    transactions `t` LEFT JOINed to annotations `a`, so the summary can never
    count different rows from the list it describes."""
    params: list = []
    conditions: list[str] = []

    if unannotated:
        conditions.append("a.id IS NULL")

    if q:
        pattern = f"%{_escape_like(q.lower())}%"
        clauses = [
            "LOWER(t.raw_description) LIKE ? ESCAPE '\\'",
            "LOWER(a.merchant) LIKE ? ESCAPE '\\'",
            "(json_valid(t.upi_meta) AND LOWER(json_extract(t.upi_meta, '$.note')) LIKE ? ESCAPE '\\')",
            # The names the user gave: this payment's own, or its payee's.
            "LOWER(t.display_name) LIKE ? ESCAPE '\\'",
            "t.counterparty_key IN (SELECT counterparty_key FROM payee_names "
            "WHERE LOWER(name) LIKE ? ESCAPE '\\')",
        ]
        params += [pattern] * 5
        # A numeric query also matches on amount (exact or, for a whole-rupee
        # query, the rupee part), so "1200" finds the ₹1,200 transaction.
        amount = _parse_amount_query(q)
        if amount is not None:
            if "." in q:
                clauses.append("t.amount = ?")
                params.append(amount)
            else:
                clauses.append("CAST(t.amount AS INTEGER) = ?")
                params.append(int(amount))
        conditions.append("(" + " OR ".join(clauses) + ")")

    if categories:
        # Uncategorized is "not sorted" (common.unsorted_sql), which includes
        # rows with no annotation at all, so it matches what Insights counts.
        named = [c for c in categories if c != UNCATEGORIZED]
        clauses = []
        if named:
            clauses.append(f"a.category IN ({','.join('?' * len(named))})")
            params += named
        if len(named) != len(categories):
            clauses.append(unsorted_sql("a"))
        conditions.append("(" + " OR ".join(clauses) + ")")

    if sources:
        placeholders = ",".join("?" * len(sources))
        conditions.append(f"a.source IN ({placeholders})")
        params += sources

    if merchant:
        conditions.append("a.merchant = ?")
        params.append(merchant)

    if payee_keys:
        # The key a Money Map row was grouped on (insights.py: counterparty
        # key, else the merchant), so a drill lists exactly the payments the
        # row adds up, whatever anyone has since called them.
        conditions.append(f"UPPER(COALESCE(t.counterparty_key, a.merchant)) IN ({','.join('?' * len(payee_keys))})")
        params += [k.upper() for k in payee_keys]

    if person_token:
        # Person→transaction matching is token-based (VPA-exact or name-prefix on
        # counterparty_key), not a stored FK, so resolve to ids the same way the
        # People page and the pipeline do, then filter on them. An empty match
        # forces an empty result rather than matching everything.
        from finance_copilot.db.queries.people import matched_transaction_ids

        ids = matched_transaction_ids(conn, person_token)
        if ids:
            placeholders = ",".join("?" * len(ids))
            conditions.append(f"t.id IN ({placeholders})")
            params += ids
        else:
            conditions.append("1 = 0")

    if statement_id:
        conditions.append("t.statement_id = ?")
        params.append(statement_id)

    if month:
        conditions.append("strftime('%Y-%m', t.txn_date) = ?")
        params.append(month)

    if min_amount is not None:
        conditions.append("t.amount >= ?")
        params.append(min_amount)
    if max_amount is not None:
        conditions.append("t.amount <= ?")
        params.append(max_amount)

    return conditions, params


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
    payee_keys: list[str] | None = None,
    person_token: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    sort: str = "date",
    order: str = "desc",
    after: str | None = None,
    limit: int | None = None,
) -> list[TxnRow]:
    """Return transactions as a list of dicts, with optional filters.

    include_annotation joins each row's annotation (same shape as the per-id
    endpoint: annotation_id, category, ...) so list pages need one round-trip.
    q is a case-insensitive substring match over the raw description, the
    annotated merchant, the UPI note and the names the user gave. payee_keys
    are the grouping keys insights rows carry (counterparty key, else merchant).
    sources/merchant filter on annotation fields, so they only ever match annotated rows; the category filter does
    too, except that Uncategorized also matches rows with no annotation.
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

    conditions, params = _filter_conditions(
        conn, statement_id=statement_id, month=month, unannotated=unannotated, q=q,
        categories=categories, sources=sources, merchant=merchant,
        payee_keys=payee_keys, person_token=person_token, min_amount=min_amount, max_amount=max_amount,
    )
    needs_annotation_join = unannotated or q or categories or sources or merchant or payee_keys
    if needs_annotation_join and not include_annotation:
        query += " LEFT JOIN annotations a ON a.transaction_id = t.id"

    # Sort key + direction, always tie-broken by id for a stable keyset order.
    sort_col = "t.amount" if sort == "amount" else "t.txn_date"
    direction = "ASC" if order == "asc" else "DESC"
    cmp = ">" if direction == "ASC" else "<"

    if after:
        cursor = conn.execute(
            "SELECT txn_date, amount, id FROM transactions WHERE id = ?", (after,)
        ).fetchone()
        if cursor is not None:
            cursor_val = cursor["amount"] if sort == "amount" else cursor["txn_date"]
            # Keyset on (sort_col, id) so pagination follows the chosen order.
            conditions.append(f"({sort_col}, t.id) {cmp} (?, ?)")
            params += [cursor_val, cursor["id"]]

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += f" ORDER BY {sort_col} {direction}, t.id {direction}"

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def summarize_transactions(
    conn: sqlite3.Connection,
    statement_id: str | None = None,
    month: str | None = None,
    unannotated: bool = False,
    q: str | None = None,
    categories: list[str] | None = None,
    sources: list[str] | None = None,
    merchant: str | None = None,
    payee_keys: list[str] | None = None,
    person_token: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
) -> dict:
    """Count and debit/credit totals of exactly the rows list_transactions
    would return for the same filters (all pages, not just the first)."""
    conditions, params = _filter_conditions(
        conn, statement_id=statement_id, month=month, unannotated=unannotated, q=q,
        categories=categories, sources=sources, merchant=merchant,
        payee_keys=payee_keys, person_token=person_token, min_amount=min_amount, max_amount=max_amount,
    )
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n,
               COALESCE(SUM(CASE WHEN t.debit_credit = 'debit' THEN t.amount END), 0) AS money_out,
               COALESCE(SUM(CASE WHEN t.debit_credit = 'credit' THEN t.amount END), 0) AS money_in
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id{where}
        """,
        params,
    ).fetchone()
    return {
        "count": row["n"],
        "money_out": round(row["money_out"], 2),
        "money_in": round(row["money_in"], 2),
    }


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

    categories = distinct("category")
    # Rows with no annotation are filterable as Uncategorized, so offer it
    # whenever any row in scope is unsorted.
    has_unsorted = conn.execute(
        f"""
        SELECT 1 FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE {unsorted_sql("a")}{where}
        LIMIT 1
        """,
        params,
    ).fetchone()
    if has_unsorted and UNCATEGORIZED not in categories:
        categories = sorted([*categories, UNCATEGORIZED])
    return {"categories": categories, "sources": distinct("source")}
