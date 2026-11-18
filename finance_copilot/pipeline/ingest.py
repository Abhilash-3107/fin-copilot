"""PDF → parsed transactions: invoke parser, persist statements and transactions."""
from __future__ import annotations

import calendar
import hashlib
import logging
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from finance_copilot.db.connection import get_migrated_db, write_transaction
from finance_copilot.db.queries.accounts import account_labels, label_for_statement, resolve_account
from finance_copilot.db.queries.transactions import (
    balance_chain_check,
    insert_statement,
    insert_transaction,
)
from finance_copilot.models.transaction import Statement, Transaction
from finance_copilot.parsers.description import KNOWN_SHAPES
from finance_copilot.parsers.period import MAX_PERIOD_DAYS
from finance_copilot.parsers.registry import detect_parser
from finance_copilot.pipeline.dedup import split_new_rows

logger = logging.getLogger(__name__)

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
    """Count consecutive running-balance pairs consistent with the amount between them.

    Used only to tell which way a same-day statement runs. A payment with no
    balance restarts the chain here, on purpose: the amount of that payment sits
    between the two balances around it, so comparing them would call a true chain
    broken in one direction. `balance_chain_check` (db/queries/transactions.py), the
    check a statement is shown and stored with, skips such a payment instead,
    because that is the rule every stored ledger was backfilled with; the two
    answer different questions and must not be merged."""
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


def month_name(statement_month: str) -> str:
    """'2026-10' as 'October 2026', the way every screen writes a month."""
    try:
        year, month = statement_month.split("-")
        return f"{calendar.month_name[int(month)]} {int(year)}"
    except (ValueError, IndexError):
        return statement_month


def _rupees(amount: float) -> str:
    """A rupee figure with the sign in front of the rupee sign, like the UI writes it."""
    return f"{'-' if amount < 0 else ''}₹{abs(amount):,.2f}"


def check_continuity(conn: sqlite3.Connection, statement: Statement) -> list[str]:
    """Cross-statement balance continuity warnings for a newly ingested statement.

    A statement's opening balance must equal the closing balance of the statement
    immediately before it (and symmetrically for the one after, since uploads can
    arrive out of order). A mismatch means a missing statement or a bad parse.

    The chain only exists within one account, so neighbours are scoped to the
    statement's account (db/queries/accounts.py decides which account a
    statement belongs to, including the "number could not be read" cases).
    """
    warnings: list[str] = []
    if statement.period_start is None or statement.period_end is None:
        return warnings

    def _mismatch(a: float | None, b: float | None) -> bool:
        return a is not None and b is not None and abs(a - b) > _BALANCE_TOLERANCE

    def _covered(day: date) -> bool:
        """Another statement of the account runs over this day: the file overlaps it
        there, so it continues that statement, not whichever one ends before it."""
        return conn.execute(
            "SELECT 1 FROM statements WHERE account_id = ? AND id != ? "
            "AND period_start <= ? AND period_end >= ? LIMIT 1",
            (statement.account_id, statement.id, day.isoformat(), day.isoformat()),
        ).fetchone() is not None

    prev = None if _covered(statement.period_start) else conn.execute(
        "SELECT statement_month, closing_balance FROM statements "
        "WHERE period_end < ? AND id != ? AND account_id = ? "
        "ORDER BY period_end DESC LIMIT 1",
        (statement.period_start.isoformat(), statement.id, statement.account_id),
    ).fetchone()
    if prev and _mismatch(prev["closing_balance"], statement.opening_balance):
        warnings.append(
            f"Opening balance {_rupees(statement.opening_balance)} doesn't match the "
            f"{month_name(prev['statement_month'])} closing balance {_rupees(prev['closing_balance'])} - "
            "a statement in between may be missing."
        )

    nxt = None if _covered(statement.period_end) else conn.execute(
        "SELECT statement_month, opening_balance FROM statements "
        "WHERE period_start > ? AND id != ? AND account_id = ? "
        "ORDER BY period_start ASC LIMIT 1",
        (statement.period_end.isoformat(), statement.id, statement.account_id),
    ).fetchone()
    if nxt and _mismatch(nxt["opening_balance"], statement.closing_balance):
        warnings.append(
            f"Closing balance {_rupees(statement.closing_balance)} doesn't match the "
            f"{month_name(nxt['statement_month'])} opening balance {_rupees(nxt['opening_balance'])} - "
            "a statement in between may be missing."
        )

    return warnings


def check_description_formats(conn: sqlite3.Connection, statement: Statement) -> list[str]:
    """Warn when UPI lines in a new statement use a layout the parser doesn't fully
    understand. Such rows still ingest (raw text is kept), but identity, learned
    rules, and retrieval degrade for them, so the user and the logs should know
    instead of finding out through a fuller review queue."""
    known = sorted(KNOWN_SHAPES)
    shapes = [
        r[0] for r in conn.execute(
            f"SELECT json_extract(upi_meta, '$.shape') FROM transactions "
            f"WHERE statement_id = ? AND upi_meta IS NOT NULL "
            f"AND json_extract(upi_meta, '$.shape') NOT IN ({','.join('?' * len(known))})",
            (statement.id, *known),
        )
    ]
    if not shapes:
        return []
    logger.warning(
        "statement %s: %d UPI line(s) in an unrecognized layout (shapes %s)",
        statement.id, len(shapes), sorted(set(shapes)),
    )
    noun = "payment is" if len(shapes) == 1 else "payments are"
    return [
        f"{len(shapes)} UPI {noun} in a format Finance Copilot doesn't fully recognise yet, "
        "so automatic sorting may be weaker for them."
    ]


class DuplicateStatementError(ValueError):
    """Raised when the exact same PDF (by content hash) was already uploaded."""

    def __init__(self, existing: dict, account_label: str):
        super().__init__(
            f"This statement was already added ({account_label}, "
            f"{month_name(existing['statement_month'])})"
        )
        self.existing = existing


class NothingNewError(ValueError):
    """Raised when every payment of the file is already in the account it belongs to."""

    def __init__(self, count: int, account_label: str):
        what = (
            "the one payment in this file is"
            if count == 1
            else f"all {count} payments in this file are"
        )
        super().__init__(f"Nothing new: {what} already in {account_label}.")


class _AllAlreadyIn(Exception):
    """Inside the write step: every payment of the file is in the account. It rolls
    the step back; ingest_pdf then names the account as it stands after that."""

    def __init__(self, count: int, account_id: str):
        super().__init__()
        self.count = count
        self.account_id = account_id


class PasswordRequiredError(ValueError):
    """Raised when the PDF is encrypted and no password, or the wrong one, was given."""

    def __init__(self, password_given: bool):
        super().__init__(
            "That password didn't unlock this PDF. Check it and try again."
            if password_given
            else "This PDF is password-protected. Enter its password and upload again."
        )
        self.password_given = password_given


def _check_unlocks(pdf_path: str, password: str | None) -> None:
    """Fail with a clear message when the PDF is locked, before parser detection.

    Parsers' detect() swallows open errors (a locked PDF just "isn't theirs"),
    which would otherwise surface as a misleading "no parser found".
    """
    try:
        pdfplumber.open(pdf_path, password=password).close()
    except Exception as exc:
        # pdfplumber wraps pdfminer's error as the first arg of PdfminerException.
        # Any other open failure is left to parser detection to report.
        if any(isinstance(arg, PDFPasswordIncorrect) for arg in exc.args):
            raise PasswordRequiredError(password_given=bool(password)) from exc


# A statement can start a little before its first payment and end a little after
# its last, not by months. A quiet account, whose payments cover a few days of a
# month it printed in full, can have its real period dropped by this window; that
# is deliberate: a wrong period is worse than none.
_PRINTED_PERIOD_SLACK_DAYS = 31


def _sane_printed_period(
    printed: tuple[date, date] | None, first_payment: date, last_payment: date
) -> tuple[date, date] | None:
    """The printed period, kept only when it holds every payment, starts no more
    than a month before the first and ends no more than a month after the last,
    and covers no more than a year. Anything else was misread (a date from
    somewhere else on the page), and a wrong period is worse than none."""
    if printed is None:
        return None
    start, end = printed
    slack = timedelta(days=_PRINTED_PERIOD_SLACK_DAYS)
    if (
        start <= first_payment
        and last_payment <= end
        and start >= first_payment - slack
        and end <= last_payment + slack
        and (end - start).days < MAX_PERIOD_DAYS
    ):
        return printed
    logger.warning(
        "ignoring the printed period %s to %s: payments run from %s to %s",
        start, end, first_payment, last_payment,
    )
    return None


def _refuse_duplicate(conn: sqlite3.Connection, file_sha256: str) -> None:
    """Raise DuplicateStatementError when this exact file is already a statement."""
    existing = conn.execute(
        "SELECT * FROM statements WHERE file_sha256 = ?", (file_sha256,)
    ).fetchone()
    if existing:
        raise DuplicateStatementError(
            dict(existing), label_for_statement(account_labels(conn), existing)
        )


def ingest_pdf(
    pdf_path: str,
    password: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> Statement:
    """Parse a PDF and persist the statement + all transactions to the DB.

    Returns the created Statement. Raises ValueError if no parser recognises the
    PDF, DuplicateStatementError if this exact file was already ingested, or
    NothingNewError if the account already holds every payment in it. A file that
    overlaps what the account has adds only its new payments (pipeline/dedup.py);
    the statement records what its file held and what it added.
    """
    file_sha256 = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()

    _conn = conn or get_migrated_db()
    try:
        _refuse_duplicate(_conn, file_sha256)  # the quick refusal, before any parsing

        _check_unlocks(pdf_path, password)
        parser = detect_parser(pdf_path, password=password)
        if parser is None:
            raise ValueError(
                "This PDF has no table of payments that Finance Copilot can read. "
                "Kotak statements are supported, and other banks work when the "
                "statement has a running-balance column."
            )

        parsed = parser.parse(pdf_path, password=password)
        transactions, opening_balance, closing_balance = _split_balance_artifacts(parsed)
        if not transactions:
            raise ValueError("No payments were found in this statement.")

        period_start = min(t.txn_date for t in transactions)
        period_end = max(t.txn_date for t in transactions)
        printed = _sane_printed_period(
            parser.extract_period(pdf_path, password=password), period_start, period_end
        )
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
            printed_start=printed[0] if printed else None,
            printed_end=printed[1] if printed else None,
        )

        # The balance check is the file's, over every payment it printed: the
        # ones that were new are not a chain when an earlier file had the rest.
        statement.rows_in_file = len(transactions)
        statement.balance_checked, statement.balance_consistent = balance_chain_check(
            (t.running_balance, t.amount, t.debit_credit) for t in transactions
        )

        # Choosing the account and writing the statement are one step under the
        # write lock: two statements arriving together for a new account must
        # not each start one.
        try:
            with write_transaction(_conn):
                # Looked again under the lock: the same file arriving twice at once
                # passed the look above in both requests, and the loser would
                # otherwise write against the winner's account.
                _refuse_duplicate(_conn, file_sha256)
                statement.account_id, statement.account_is_new = resolve_account(
                    _conn, statement.bank_name, statement.account_ref
                )
                split = split_new_rows(_conn, statement.account_id, transactions)
                if not split.new:
                    raise _AllAlreadyIn(len(transactions), statement.account_id)
                statement.rows_added = len(split.new)
                if split.overlaps_without_match:
                    statement.notices.append(
                        f"This file covers dates already in {account_labels(_conn)[statement.account_id]}, "
                        "but none of its payments match. If it is another account, "
                        "move it with Move to account."
                    )
                insert_statement(_conn, statement)
                for txn in split.new:
                    txn.statement_id = statement.id
                    insert_transaction(_conn, txn)
        except _AllAlreadyIn as nothing:
            # The refusal rolled back anything the account lookup did (a number
            # it took on), so the account is named as it is, not as this file
            # would have left it.
            raise NothingNewError(
                nothing.count, account_labels(_conn)[nothing.account_id]
            ) from None
    finally:
        if conn is None:
            _conn.close()

    return statement
