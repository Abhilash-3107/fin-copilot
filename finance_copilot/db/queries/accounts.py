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
# parser, as "hdfc (auto)". That is how it was read, not which bank it is: a
# value carrying the marker is a parser's key, never text a person typed.
# Released 0.1.2 wrote the name it matched in the header, and one of those
# names is not the bank key: "state bank (auto)" is SBI.
_AUTO_MARKER = "(auto)"
_MARKED_ALIASES = {"state bank": "sbi"}

# The bank key of a statement the generic parser could not place. It is not one
# bank, so a numbered statement of it never guesses which account it belongs to.
UNKNOWN_BANK = "generic"


def bank_key(bank_name: str) -> str:
    """The bank an account belongs to: a statement's bank_name trimmed, lower case
    and without the generic parser's marker, so "hdfc (auto)", "HDFC " and a later
    dedicated "hdfc" parser are one bank, and "state bank (auto)" is "sbi".
    Migration 041 and e2e/upgrade/accounts-oracle.js group with the same rule."""
    key = bank_name.strip().lower()
    if not key.endswith(_AUTO_MARKER):
        return key
    key = key[: -len(_AUTO_MARKER)].strip()
    return _MARKED_ALIASES.get(key, key)


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


def _plain_number(ref: str) -> str:
    return ref.replace("-", "").replace(" ", "").upper()


def _unmasked_tail(plain: str) -> str:
    """The digits a number prints after its last mask character ("XXXXXX4321" -> "4321");
    all of it when nothing is masked."""
    tail = []
    for char in reversed(plain):
        if char in "X*":
            break
        tail.append(char)
    return "".join(reversed(tail))


def same_number(a: str | None, b: str | None) -> bool:
    """Whether two printed account numbers are one account's. The one rule for a PDF
    and a spreadsheet: equal, or one (or both) masked and the digits the mask
    leaves agree, four at least ("XXXXXX4321" is "1234564321"). Two full numbers
    are the same only when equal."""
    if not a or not b:
        return False
    a, b = _plain_number(a), _plain_number(b)
    if a == b:
        return True
    masked = [x for x in (a, b) if any(c in "X*" for c in x)]
    if not masked:
        return False
    keep = min(len(_unmasked_tail(x)) for x in masked)
    return keep >= 4 and a[-keep:] == b[-keep:] and a[-keep:].isdigit()


def _bank_fits(account_bank: str, bank: str) -> bool:
    """The same bank, or one of them is unknown (the generic parser could not place it)."""
    return account_bank == bank or UNKNOWN_BANK in (account_bank, bank)


def accounts_with_number(
    conn: sqlite3.Connection, bank_name: str, account_ref: str | None
) -> list[sqlite3.Row]:
    """The accounts a printed number names, by the shared rule: the same bank (an
    unknown bank fits any), and the same number. Accounts holding the very same
    number are returned alone when there are any, so a full number is not made
    ambiguous by a masked one that also fits."""
    ref = (account_ref or "").strip()
    if not ref:
        return []
    bank = bank_key(bank_name)
    fitting = [
        a for a in conn.execute("SELECT id, bank_name, account_ref FROM accounts")
        if _bank_fits(a["bank_name"], bank) and same_number(a["account_ref"], ref)
    ]
    exact = [a for a in fitting if _plain_number(a["account_ref"]) == _plain_number(ref) and a["bank_name"] == bank]
    return exact or fitting


def _remembered_account(conn: sqlite3.Connection, bank: str, ref: str | None) -> str | None:
    """The account of the statement with this bank and number that holds the latest
    month (an unknown number matches an unknown number), when it still exists. The
    month decides, not the upload time: a statement a person re-uploads for an old
    month must not pull the next one after it. A month held twice goes by upload
    time. A statement the user moved keeps the next one of its bank and number
    company."""
    rows = conn.execute(
        "SELECT s.bank_name, s.account_id FROM statements s JOIN accounts a ON a.id = s.account_id "
        "WHERE NULLIF(TRIM(s.account_ref), '') IS ? "
        "ORDER BY s.period_end DESC, s.uploaded_at DESC, s.rowid DESC",
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

    What the user has done wins: the account of the statement with the same bank
    and number that holds the latest month, wherever they put it. A statement
    with no number follows it only into an account that has no number either:
    which of several numbered accounts it belongs to is not something to guess.
    Otherwise, never guess between accounts:
    - Same bank and same number (one rule, `same_number`: a masked number is a full
      one when the digits it leaves agree): that account. An account of unknown bank
      is the account of a known bank that names its number, and takes that bank.
      Two accounts matching start a new one.
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
    if remembered and (
        ref is not None
        or conn.execute("SELECT account_ref FROM accounts WHERE id = ?", (remembered,)).fetchone()[0] is None
    ):
        return remembered, False

    accounts = conn.execute(
        "SELECT id, account_ref FROM accounts WHERE bank_name = ?", (bank,)
    ).fetchall()

    if ref:
        for account in accounts:
            if account["account_ref"] == ref:
                return account["id"], False
        named = accounts_with_number(conn, bank, ref)
        if len(named) == 1:
            if named[0]["bank_name"] == UNKNOWN_BANK and bank != UNKNOWN_BANK:
                conn.execute("UPDATE accounts SET bank_name = ? WHERE id = ?", (bank, named[0]["id"]))
            return named[0]["id"], False
        if named:
            # Two accounts carry this number: which one is not for us to guess.
            return create_account(conn, bank, ref), True
        if bank != UNKNOWN_BANK and len(accounts) == 1 and accounts[0]["account_ref"] is None:
            conn.execute("UPDATE accounts SET account_ref = ? WHERE id = ?", (ref, accounts[0]["id"]))
            return accounts[0]["id"], False
    else:
        if len(accounts) == 1:
            return accounts[0]["id"], False
        unnumbered = [a for a in accounts if a["account_ref"] is None]
        if len(unnumbered) == 1:
            return unnumbered[0]["id"], False

    return create_account(conn, bank, ref), True


def create_account(
    conn: sqlite3.Connection, bank_name: str, account_ref: str | None, name: str | None = None
) -> str:
    """Start an account for this bank and number. It does not look for an existing
    one: resolve_account does, and a user splitting a statement out wants a new one
    even where the same bank and number already has an account."""
    account_id = f"acc_{ulid.ULID()}"
    conn.execute(
        "INSERT INTO accounts (id, bank_name, account_ref, name) VALUES (?, ?, ?, ?)",
        (account_id, bank_key(bank_name), (account_ref or "").strip() or None, (name or "").strip() or None),
    )
    return account_id


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


def with_account(conn: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    """Add the account a payment sits in (`account_id`, `account_label`) to payment rows,
    through their statements, so a list that shows several accounts can say which. The
    words are the server's, like every account label."""
    ids = sorted({row["statement_id"] for row in rows if row.get("statement_id")})
    if not ids:
        return rows
    by_statement: dict[str, str | None] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        by_statement.update({
            r["id"]: r["account_id"]
            for r in conn.execute(
                f"SELECT id, account_id FROM statements WHERE id IN ({','.join('?' * len(chunk))})", chunk
            )
        })
    labels = account_labels(conn)
    for row in rows:
        account_id = by_statement.get(row.get("statement_id"))
        row["account_id"] = account_id
        row["account_label"] = labels.get(account_id)
    return rows
