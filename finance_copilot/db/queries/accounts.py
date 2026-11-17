"""Accounts: where money sits.

An account's identity is its bank plus the account number as the statement
prints it. The user's own name for it is optional; until they set one the label
is derived, so every screen shows the same words. This module owns both: which
account a new statement belongs to, and what an account is called.

Statements belong to one account (statements.account_id). An account with no
statements does not exist: whatever removes a statement's last link to its
account deletes the account too (delete_empty_accounts).
"""
from __future__ import annotations

import sqlite3

import ulid

from finance_copilot.parsers.bank_identity import bank_display_name

# Between the bank and the last digits: "Kotak ··8074". The one separator the
# app uses for a masked number.
MASK = "··"

NAME_MAX_LENGTH = 60

# The generic parser marks a bank it read by its columns, not by a dedicated
# parser, as "hdfc (auto)". That is how it was read, not which bank it is.
_AUTO_MARKER = "(auto)"

# The bank key of a statement the generic parser could not place. It is not one
# bank, so a numbered statement of it never guesses which account it belongs to.
UNKNOWN_BANK = "generic"


def bank_key(bank_name: str) -> str:
    """The bank an account belongs to: a statement's bank_name trimmed, lower case
    and without the generic parser's marker, so "hdfc (auto)", "HDFC " and a later
    dedicated "hdfc" parser are one bank. Migration 041 groups with the same rule."""
    key = bank_name.strip().lower()
    return key[: -len(_AUTO_MARKER)].strip() if key.endswith(_AUTO_MARKER) else key


def ref_last4(account_ref: str | None) -> str | None:
    """The last four characters of the number, or None when there is none. The
    full number is never shown."""
    digits = (account_ref or "").replace("-", "").replace(" ", "")
    return digits[-4:] if digits else None


def account_label(name: str | None, bank_name: str, account_ref: str | None) -> str:
    """What an account is called: the user's name when they set one, otherwise the
    bank and the last four digits ("Kotak ··8074", or "Kotak" with no number)."""
    if name and name.strip():
        return name.strip()
    bank = bank_display_name(bank_key(bank_name))
    last4 = ref_last4(account_ref)
    return f"{bank} {MASK}{last4}" if last4 else bank


def _remembered_account(conn: sqlite3.Connection, bank: str, ref: str | None) -> str | None:
    """The account of the most recent statement with this bank and number (an
    unknown number matches an unknown number), when it still exists. A statement
    the user moved keeps the next one of its bank and number company."""
    rows = conn.execute(
        "SELECT s.bank_name, s.account_id FROM statements s JOIN accounts a ON a.id = s.account_id "
        "WHERE NULLIF(TRIM(s.account_ref), '') IS ? ORDER BY s.uploaded_at DESC, s.rowid DESC",
        (ref,),
    )
    for row in rows:
        if bank_key(row["bank_name"]) == bank:
            return row["account_id"]
    return None


def resolve_account(
    conn: sqlite3.Connection, bank_name: str, account_ref: str | None
) -> tuple[str, bool]:
    """The account a new statement belongs to, and whether this call created it.

    What the user has done wins: the account of the latest statement with the
    same bank and number, wherever they put it. Otherwise, never guess between
    accounts:
    - Same bank and same number: that account.
    - A number and the bank's only account has none (older statements printed no
      number): that account takes the number. Not for an unknown bank, where an
      unnumbered account may be a different bank's.
    - No number and the bank has one account: that account. With several, the
      statements whose number could not be read gather in one "unknown number"
      account, which the user can move them out of.
    - Anything else starts a new account.
    """
    bank = bank_key(bank_name)
    ref = (account_ref or "").strip() or None

    remembered = _remembered_account(conn, bank, ref)
    if remembered:
        return remembered, False

    accounts = conn.execute(
        "SELECT id, account_ref FROM accounts WHERE bank_name = ?", (bank,)
    ).fetchall()

    if ref:
        for account in accounts:
            if account["account_ref"] == ref:
                return account["id"], False
        if bank != UNKNOWN_BANK and len(accounts) == 1 and accounts[0]["account_ref"] is None:
            conn.execute("UPDATE accounts SET account_ref = ? WHERE id = ?", (ref, accounts[0]["id"]))
            return accounts[0]["id"], False
    else:
        if len(accounts) == 1:
            return accounts[0]["id"], False
        unnumbered = [a for a in accounts if a["account_ref"] is None]
        if len(unnumbered) == 1:
            return unnumbered[0]["id"], False

    account_id = f"acc_{ulid.ULID()}"
    conn.execute(
        "INSERT INTO accounts (id, bank_name, account_ref) VALUES (?, ?, ?)",
        (account_id, bank, ref),
    )
    return account_id, True


def delete_empty_accounts(conn: sqlite3.Connection) -> int:
    """Remove accounts that no statement belongs to. Returns how many went."""
    return conn.execute(
        "DELETE FROM accounts WHERE id NOT IN "
        "(SELECT account_id FROM statements WHERE account_id IS NOT NULL)"
    ).rowcount


def label_for_statement(
    labels: dict[str, str], statement: sqlite3.Row | dict
) -> str:
    """The label of a statement's account. A statement without an account (only a
    row written around ingest, such as a test's) is named from its own bank and
    number, so a screen never shows a bare parser key."""
    found = labels.get(statement["account_id"])
    if found:
        return found
    return account_label(None, statement["bank_name"], statement["account_ref"])


def account_labels(conn: sqlite3.Connection) -> dict[str, str]:
    """{account id: label} for every account."""
    return {
        r["id"]: account_label(r["name"], r["bank_name"], r["account_ref"])
        for r in conn.execute("SELECT id, name, bank_name, account_ref FROM accounts")
    }


_ACCOUNT_SQL = """
SELECT a.id, a.name, a.bank_name, a.account_ref,
       COUNT(s.id) AS statement_count,
       (SELECT MIN(t.txn_date) FROM transactions t JOIN statements x ON x.id = t.statement_id
         WHERE x.account_id = a.id) AS first_date,
       (SELECT MAX(t.txn_date) FROM transactions t JOIN statements x ON x.id = t.statement_id
         WHERE x.account_id = a.id) AS last_date,
       (SELECT x.closing_balance FROM statements x
         WHERE x.account_id = a.id AND x.closing_balance IS NOT NULL AND x.period_end IS NOT NULL
         ORDER BY x.period_end DESC, x.uploaded_at DESC LIMIT 1) AS balance,
       (SELECT x.period_end FROM statements x
         WHERE x.account_id = a.id AND x.closing_balance IS NOT NULL AND x.period_end IS NOT NULL
         ORDER BY x.period_end DESC, x.uploaded_at DESC LIMIT 1) AS balance_as_of
FROM accounts a
JOIN statements s ON s.account_id = a.id
{where}
GROUP BY a.id
ORDER BY MAX(s.period_end) DESC, a.created_at DESC, a.id
"""


def _account_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "account_label": account_label(row["name"], row["bank_name"], row["account_ref"]),
        "name": row["name"],
        "bank_name": row["bank_name"],
        "account_ref_last4": ref_last4(row["account_ref"]),
        "statement_count": row["statement_count"],
        "first_date": row["first_date"],
        "last_date": row["last_date"],
        "balance": row["balance"],
        "balance_as_of": row["balance_as_of"],
    }


def list_accounts(conn: sqlite3.Connection) -> list[dict]:
    """Every account that holds a statement, latest activity first. Never carries
    the full account number."""
    return [_account_row(r) for r in conn.execute(_ACCOUNT_SQL.format(where=""))]


def get_account(conn: sqlite3.Connection, account_id: str) -> dict | None:
    row = conn.execute(_ACCOUNT_SQL.format(where="WHERE a.id = ?"), (account_id,)).fetchone()
    return _account_row(row) if row else None


def set_account_name(conn: sqlite3.Connection, account_id: str, name: str | None) -> None:
    """Name an account. Blank clears it, back to the derived label."""
    cleaned = (name or "").strip() or None
    conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (cleaned, account_id))
