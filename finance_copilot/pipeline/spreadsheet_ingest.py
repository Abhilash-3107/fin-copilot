"""A CSV, XLSX or XLS statement: from bytes to a statement in an account.

Front end of the shared ingest core (pipeline/ingest.py, ingest_parsed): this
module reads the file (parsers/spreadsheet.py), decides which columns are which
and which account the file belongs to, and asks the person only when it cannot
decide. Asking is stateless: NeedsAnswer carries what the form needs and nothing
is stored; the answer comes back with the same file.

Which columns, first match wins: the person's choice sent with the file, the
layout remembered for this header (migration 045), what the header names. Which
account: the person's choice, the account whose number the rows above the table
name, the account the layout remembered. A remembered layout never overrides a
number above the table that names a different account.
"""
from __future__ import annotations

import hashlib
import sqlite3

from finance_copilot.db.connection import get_migrated_db
from finance_copilot.db.queries.accounts import (
    NAME_MAX_LENGTH,
    UNKNOWN_BANK,
    create_account,
    list_accounts,
    ref_last4,
)
from finance_copilot.db.queries.import_layouts import get_layout, remember_layout
from finance_copilot.models.transaction import Statement
from finance_copilot.parsers import spreadsheet as sheet
from finance_copilot.parsers.base import account_ref_in_text
from finance_copilot.pipeline.ingest import (
    ParsedFile,
    _refuse_duplicate,
    ingest_parsed,
)

SPREADSHEET_PARSER_VERSION = "spreadsheet 0.1.0"

_PREVIEW_ROWS = 5


class NeedsAnswer(ValueError):
    """The file cannot land yet: its columns, its account, or both need the person.
    Nothing was stored. `body` is what the form shows."""

    def __init__(self, needs: list[str], body: dict):
        super().__init__(body["detail"])
        self.needs = needs
        self.body = body


def _plain_ref(ref: str) -> str:
    return ref.replace("-", "").replace(" ", "").upper()


def _same_number(a: str | None, b: str | None) -> bool:
    """Two printed account numbers are one: equal, or one masked and the other's
    last four digits the same (a CSV's "XXXXXX8074" and a PDF's "3250508074")."""
    if not a or not b:
        return False
    a, b = _plain_ref(a), _plain_ref(b)
    if a == b:
        return True
    masked = any(c in "X*" for c in a + b)
    return masked and len(a) >= 4 and len(b) >= 4 and a[-4:] == b[-4:] and a[-4:].isdigit()


def _account_with_number(conn: sqlite3.Connection, ref: str | None) -> str | None:
    """The one account that number names; None when none or several do."""
    if not ref:
        return None
    found = [
        r["id"] for r in conn.execute("SELECT id, account_ref FROM accounts")
        if _same_number(r["account_ref"], ref)
    ]
    return found[0] if len(found) == 1 else None


def _exists(conn: sqlite3.Connection, account_id: str | None) -> dict | None:
    if not account_id:
        return None
    row = conn.execute(
        "SELECT id, bank_name, account_ref FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    return dict(row) if row else None


def _decide_account(
    conn: sqlite3.Connection, ref: str | None, remembered_id: str | None
) -> str | None:
    """The account a file belongs to without being asked, or None."""
    named = _account_with_number(conn, ref)
    if named:
        return named
    kept = _exists(conn, remembered_id)
    if kept is None:
        return None
    # A number above the table that this account does not carry is a different account.
    if ref and kept["account_ref"] and not _same_number(kept["account_ref"], ref):
        return None
    return kept["id"]


def _needs_body(
    conn: sqlite3.Connection, table: sheet.Table, needs: list[str], guess: dict[str, int],
    message: str, ref: str | None,
) -> dict:
    return {
        "detail": message,
        "needs": needs,
        "columns": table.columns,
        "preview": [
            [(row[i] if i < len(row) else "") for i in range(table.width)]
            for row in table.data_rows[:_PREVIEW_ROWS]
        ],
        "guess": guess,
        "parsed": sheet.preview(table, guess, _PREVIEW_ROWS),
        "accounts": [
            {"id": a["id"], "account_label": a["account_label"]} for a in list_accounts(conn)
        ],
        "file_account_last4": ref_last4(ref),
    }


def _ask(needs: list[str]) -> str:
    if "mapping" in needs:
        return "We couldn't tell which column is which. Check the columns, and choose the account."
    return "Which account is this file from? Choose it once and the next file like it will go there by itself."


def ingest_spreadsheet(
    conn: sqlite3.Connection | None,
    contents: bytes,
    *,
    mapping: str | None = None,
    account_id: str | None = None,
    new_account_name: str | None = None,
    confirm_overlap: bool = False,
) -> Statement:
    """Read a spreadsheet statement and add it. `mapping` is the person's column
    choice as the form sends it (JSON {role: column}); `account_id` or
    `new_account_name` their account. Raises SpreadsheetError (the file cannot be
    read, in plain words), NeedsAnswer (ask; nothing stored), OverlapNeedsConfirmation
    (the file mostly matches nothing over days the account covers, and
    `confirm_overlap` was not given), DuplicateStatementError, NothingNewError."""
    file_sha256 = hashlib.sha256(contents).hexdigest()
    own = conn is None
    _conn = conn or get_migrated_db()
    try:
        _refuse_duplicate(_conn, file_sha256)
        table = sheet.read_table(contents)
        if not sheet.has_payment_rows(table):
            raise sheet.SpreadsheetError(
                "No table of payments was found in this file. A statement needs a date, a "
                "description and an amount on each row."
            )
        ref = account_ref_in_text(table.preamble)
        layout = get_layout(_conn, table.signature)

        if mapping is not None:
            chosen = sheet.parse_mapping_field(mapping)
        elif layout is not None:
            chosen = layout["mapping"]
        elif table.recognised is not None:
            chosen = sheet.mapping_of(table.recognised)
        else:
            chosen = None

        name = (new_account_name or "").strip()
        if account_id is not None and new_account_name is not None:
            raise ValueError("Choose an existing account or name a new one, not both.")
        if len(name) > NAME_MAX_LENGTH:
            raise ValueError(f"An account name can be at most {NAME_MAX_LENGTH} characters.")
        if new_account_name is not None and not name:
            raise ValueError("Give the new account a name, so you can tell your accounts apart.")
        if account_id is not None and _exists(_conn, account_id) is None:
            raise ValueError("That account isn't in your ledger any more. Choose the account again.")
        if account_id is not None:
            decided: str | None = account_id
        elif name:
            decided = None
        else:
            decided = _decide_account(_conn, ref, layout["account_id"] if layout else None)

        needs = []
        if chosen is None:
            needs.append("mapping")
        if decided is None and not name:
            needs.append("account")
        if needs:
            guess = chosen if chosen is not None else sheet.starting_mapping(table)
            raise NeedsAnswer(needs, _needs_body(_conn, table, needs, guess, _ask(needs), ref))

        parsed = sheet.parse_table(table, chosen)
        account = _exists(_conn, decided) if decided else None
        file = ParsedFile(
            transactions=parsed.transactions,
            bank_name=account["bank_name"] if account else (parsed.bank or UNKNOWN_BANK),
            parser_version=SPREADSHEET_PARSER_VERSION,
            account_ref=parsed.account_ref or (account["account_ref"] if account else None),
            printed=parsed.period,
        )

        def choose(c: sqlite3.Connection, statement: Statement) -> tuple[str, bool]:
            if decided is None:
                return create_account(c, statement.bank_name, parsed.account_ref, name), True
            # Looked again under the lock: it may have been deleted since.
            row = _exists(c, decided)
            if row is None:
                raise ValueError("That account isn't in your ledger any more. Choose the account again.")
            if row["account_ref"] is None and parsed.account_ref:
                c.execute("UPDATE accounts SET account_ref = ? WHERE id = ?", (parsed.account_ref, decided))
            return decided, False

        def landed(c: sqlite3.Connection, statement: Statement) -> None:
            remember_layout(c, table.signature, chosen, statement.account_id)

        return ingest_parsed(
            _conn, file_sha256, file, choose,
            hold_mostly_unmatched=not confirm_overlap, on_landed=landed,
        )
    finally:
        if own:
            _conn.close()
