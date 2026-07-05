"""PDF → parsed transactions: invoke parser, persist statements and transactions."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

from src.db.connection import get_db
from src.db.queries.transactions import insert_statement, insert_transaction
from src.models.transaction import Statement, Transaction
from src.parsers.registry import detect_parser

# Statement rows that describe the balance itself rather than a money movement.
# Some bank formats (e.g. Kotak until early 2026) print these as dated rows with
# an amount, so parsers emit them as transactions; storing them as credits
# double-counts the previous month's savings as this month's income. Anchored at
# the start of the description so genuine rows that merely mention a balance
# ("Int.Pd:...Closing Balance") are not swept up.
_OPENING_ARTIFACT_RE = re.compile(
    r"^\s*(?:opening\s+balance|balance\s+(?:b/f|brought\s+forward)|b/f)(?!\w)",
    re.IGNORECASE,
)
_CLOSING_ARTIFACT_RE = re.compile(
    r"^\s*(?:closing\s+balance|balance\s+(?:c/f|carried\s+forward)|c/f)(?!\w)",
    re.IGNORECASE,
)

# Paise-level rounding noise in parsed PDF amounts.
_BALANCE_TOLERANCE = 0.01


def is_balance_artifact(txn: Transaction) -> bool:
    """True for opening/closing-balance rows that are statement metadata, not transactions."""
    return (
        _OPENING_ARTIFACT_RE.match(txn.raw_description) is not None
        or _CLOSING_ARTIFACT_RE.match(txn.raw_description) is not None
    )


def _implied_opening(txn: Transaction) -> float | None:
    """Balance before `txn`, derived by undoing it against its running balance."""
    if txn.running_balance is None:
        return None
    delta = txn.amount if txn.debit_credit == "debit" else -txn.amount
    return txn.running_balance + delta


def _chain_consistency(transactions: list[Transaction]) -> int:
    """Count consecutive running-balance pairs consistent with the amount between them."""
    ok = 0
    prev: float | None = None
    for txn in transactions:
        if txn.running_balance is None:
            prev = None
            continue
        if prev is not None:
            expected = txn.amount if txn.debit_credit == "credit" else -txn.amount
            if abs((txn.running_balance - prev) - expected) <= _BALANCE_TOLERANCE:
                ok += 1
        prev = txn.running_balance
    return ok


def _in_chain_order(transactions: list[Transaction]) -> list[Transaction]:
    """Return transactions oldest-first regardless of how the bank prints them.

    Kotak lists rows oldest-first; other banks print newest-first. Dates decide
    when they differ across the statement; for same-day statements the
    running-balance chain decides (whichever direction the chain verifies in).
    """
    if len(transactions) < 2:
        return transactions
    if transactions[0].txn_date < transactions[-1].txn_date:
        return transactions
    reversed_txns = list(reversed(transactions))
    if transactions[0].txn_date > transactions[-1].txn_date:
        return reversed_txns
    if _chain_consistency(reversed_txns) > _chain_consistency(transactions):
        return reversed_txns
    return transactions


def _split_balance_artifacts(
    parsed: list[Transaction],
) -> tuple[list[Transaction], float | None, float | None]:
    """Separate balance-artifact rows from real transactions.

    Returns (transactions oldest-first, opening_balance, closing_balance).
    Balances come from the artifact rows when present, otherwise from the
    running-balance chain of the first/last real transaction.
    """
    transactions: list[Transaction] = []
    opening: float | None = None
    closing: float | None = None

    for txn in parsed:
        if not is_balance_artifact(txn):
            transactions.append(txn)
            continue
        value = txn.running_balance if txn.running_balance is not None else txn.amount
        if _OPENING_ARTIFACT_RE.match(txn.raw_description):
            opening = value
        else:
            closing = value

    transactions = _in_chain_order(transactions)

    if opening is None and transactions:
        opening = _implied_opening(transactions[0])
    if closing is None:
        for txn in reversed(transactions):
            if txn.running_balance is not None:
                closing = txn.running_balance
                break

    return transactions, opening, closing


def check_continuity(conn: sqlite3.Connection, statement: Statement) -> list[str]:
    """Cross-statement balance continuity warnings for a newly ingested statement.

    A statement's opening balance must equal the closing balance of the statement
    immediately before it (and symmetrically for the one after, since uploads can
    arrive out of order). A mismatch means a missing statement or a bad parse.

    The chain only exists within one bank account, so neighbours are scoped to
    the same bank_name and the same account_ref. account_ref uses NULL-safe
    equality (IS): unknown-account statements chain with each other (the
    single-account case keeps working without header extraction) but never with
    a statement from an identified different account.
    """
    warnings: list[str] = []
    if statement.period_start is None or statement.period_end is None:
        return warnings

    def _mismatch(a: float | None, b: float | None) -> bool:
        return a is not None and b is not None and abs(a - b) > _BALANCE_TOLERANCE

    prev = conn.execute(
        "SELECT statement_month, closing_balance FROM statements "
        "WHERE period_end < ? AND id != ? AND bank_name = ? AND account_ref IS ? "
        "ORDER BY period_end DESC LIMIT 1",
        (statement.period_start.isoformat(), statement.id,
         statement.bank_name, statement.account_ref),
    ).fetchone()
    if prev and _mismatch(prev["closing_balance"], statement.opening_balance):
        warnings.append(
            f"Opening balance ₹{statement.opening_balance:,.2f} doesn't match the "
            f"{prev['statement_month']} closing balance ₹{prev['closing_balance']:,.2f} — "
            "a statement in between may be missing."
        )

    nxt = conn.execute(
        "SELECT statement_month, opening_balance FROM statements "
        "WHERE period_start > ? AND id != ? AND bank_name = ? AND account_ref IS ? "
        "ORDER BY period_start ASC LIMIT 1",
        (statement.period_end.isoformat(), statement.id,
         statement.bank_name, statement.account_ref),
    ).fetchone()
    if nxt and _mismatch(nxt["opening_balance"], statement.closing_balance):
        warnings.append(
            f"Closing balance ₹{statement.closing_balance:,.2f} doesn't match the "
            f"{nxt['statement_month']} opening balance ₹{nxt['opening_balance']:,.2f} — "
            "a statement in between may be missing."
        )

    return warnings


class DuplicateStatementError(ValueError):
    """Raised when the exact same PDF (by content hash) was already uploaded."""

    def __init__(self, existing: dict):
        super().__init__(
            f"This statement was already uploaded ({existing['bank_name']}, "
            f"{existing['statement_month']})"
        )
        self.existing = existing


def ingest_pdf(
    pdf_path: str,
    password: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> Statement:
    """Parse a PDF and persist the statement + all transactions to the DB.

    Returns the created Statement. Raises ValueError if no parser recognises the
    PDF, or DuplicateStatementError if this exact file was already ingested.
    """
    file_sha256 = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()

    _conn = conn or get_db()
    try:
        existing = _conn.execute(
            "SELECT * FROM statements WHERE file_sha256 = ?", (file_sha256,)
        ).fetchone()
        if existing:
            raise DuplicateStatementError(dict(existing))

        parser = detect_parser(pdf_path, password=password)
        if parser is None:
            raise ValueError(f"No parser found for PDF: {pdf_path}")

        parsed = parser.parse(pdf_path, password=password)
        transactions, opening_balance, closing_balance = _split_balance_artifacts(parsed)
        if not transactions:
            raise ValueError(f"Parser returned no transactions for: {pdf_path}")

        period_start = min(t.txn_date for t in transactions)
        period_end = max(t.txn_date for t in transactions)
        statement = Statement(
            bank_name=parser.bank_name,
            parser_version=parser.version,
            statement_month=period_start.strftime("%Y-%m"),
            period_start=period_start,
            period_end=period_end,
            file_sha256=file_sha256,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            account_ref=parser.extract_account_ref(pdf_path, password=password),
        )

        for txn in transactions:
            txn.statement_id = statement.id

        insert_statement(_conn, statement)
        for txn in transactions:
            insert_transaction(_conn, txn)
        _conn.commit()
    finally:
        if conn is None:
            _conn.close()

    return statement
