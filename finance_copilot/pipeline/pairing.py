"""Pairing: a transfer between your own accounts is not spending.

A debit in one account and a credit in another are a pair when
  - the accounts are different (both are the user's: an account exists only
    because the user added a statement of it),
  - the amounts are equal to the paisa,
  - the dates are at most WINDOW_DAYS apart (NEFT and IMPS can land a day or two
    later, a weekend adds one),
  - neither payment is already in a transfer link, and the user has not taken this
    pair apart before,
  - neither carries a label the user wrote or imported in a category other than
    Self Transfers (one they sorted as Self Transfers by hand may pair).

Those are the candidates. A pair is made by itself only when it is unambiguous
(the debit has exactly one candidate credit and that credit exactly one candidate
debit) AND there is a sign it is the user's own money, because amount and date
alone also match a friend paid and a stranger paying the same sum in:
  - the other account's number appears in the line,
  - a side is already sorted Self Transfers, by anyone,
  - a side's payee is a person marked "self" in People,
  - the line has a self-transfer word (SELF_TRANSFER_WORDS).
Anything else is left exactly as it is, and Teach Me offers the candidates
(transfer_candidates) for the user to pair or not.

Pairing writes the link and the two annotations (db/queries/transfers.py) and
nothing else: no amount, date or other stored fact changes. It is idempotent.
It runs after a statement lands, before the sorting job is queued, so sorting
skips the paired payments; a 'pair' annotation is not a machine label, so no
later stage re-decides it (pipeline/sources.py).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date

from finance_copilot.db.connection import write_transaction
from finance_copilot.db.queries.accounts import account_labels, label_for_statement
from finance_copilot.db.queries.people import SELF_RELATIONSHIP, _txn_vpa, person_token_matches
from finance_copilot.db.queries.transfers import (
    LINK_TYPE,
    SELF_CATEGORY,
    link_pair,
)
from finance_copilot.pipeline.sources import VERIFIED_SOURCES

WINDOW_DAYS = 3

# Words that say a line moves money between the user's own accounts, matched
# case-insensitively and only as whole tokens ("OWN" in "TO OWN SBI SAVINGS" but
# not in "DOWNTOWN" or "GROWN"). The phrases are the plain ways banks and people
# write it; a bare OWN or SELF already covers them, they are listed so the list
# reads as what it means. Keep it short: a word here lets a pair be made without
# asking, so it must be one that hardly ever appears on a payment to someone else.
SELF_TRANSFER_WORDS = ("own", "self", "own account", "to self", "from self")

_SELF_WORD_RE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in SELF_TRANSFER_WORDS) + r")(?![a-z0-9])",
    re.IGNORECASE,
)
_DIGIT_RUN = re.compile(r"\d{4,}")
_MIN_NUMBER_DIGITS = 4


@dataclass(frozen=True)
class Payment:
    """One payment as pairing sees it."""

    id: str
    day: date
    amount: float
    cents: int
    is_debit: bool
    account_id: str
    description: str
    counterparty_key: str | None
    upi_meta: str | None
    source: str | None      # of its annotation, None when it has none
    category: str | None

    @property
    def pairable(self) -> bool:
        """False when the user wrote or imported a label that is not Self Transfers."""
        return self.source not in VERIFIED_SOURCES or self.category == SELF_CATEGORY


@dataclass(frozen=True)
class Candidate:
    """A debit and a credit that fit the rule (a sign is a separate question)."""

    debit: Payment
    credit: Payment

    @property
    def gap_days(self) -> int:
        return abs((self.debit.day - self.credit.day).days)


def _cents(amount: float) -> int:
    return round(amount * 100)


def _load(
    conn: sqlite3.Connection, *, cents: set[int] | None = None, ids: list[str] | None = None
) -> list[Payment]:
    """Payments in an account that are in no transfer link. `cents` limits the load
    to those amounts and `ids` to those payments (Teach Me asks about a page of
    payments, not the ledger)."""
    where, params = "", []
    if cents is not None:
        where += f" AND CAST(ROUND(t.amount * 100) AS INTEGER) IN ({','.join('?' * len(cents))})"
        params += sorted(cents)
    if ids is not None:
        where += f" AND t.id IN ({','.join('?' * len(ids))})"
        params += ids
    rows = conn.execute(
        f"""
        SELECT t.id, t.txn_date, t.amount, t.debit_credit, s.account_id, t.raw_description,
               t.counterparty_key, t.upi_meta, a.source, a.category
        FROM transactions t
        JOIN statements s ON s.id = t.statement_id
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE s.account_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM transaction_links l
                          WHERE l.link_type = '{LINK_TYPE}' AND (l.txn_a = t.id OR l.txn_b = t.id)){where}
        """,
        params,
    ).fetchall()
    return [
        Payment(
            id=r["id"], day=date.fromisoformat(r["txn_date"]), amount=r["amount"], cents=_cents(r["amount"]),
            is_debit=r["debit_credit"] == "debit", account_id=r["account_id"],
            description=r["raw_description"] or "", counterparty_key=r["counterparty_key"],
            upi_meta=r["upi_meta"], source=r["source"], category=r["category"],
        )
        for r in rows
    ]


def _fits(debit: Payment, credit: Payment) -> bool:
    """Different accounts, the same amount to the paisa, dates within the window."""
    return (
        credit.account_id != debit.account_id
        and credit.cents == debit.cents
        and abs((debit.day - credit.day).days) <= WINDOW_DAYS
    )


def _candidates(conn: sqlite3.Connection, payments: list[Payment]) -> list[Candidate]:
    """Every debit and credit that fit the rule, found by amount first."""
    unpaired = {
        (r["txn_a"], r["txn_b"]) for r in conn.execute("SELECT txn_a, txn_b FROM transfer_unpaired")
    }
    credits_by_cents: dict[int, list[Payment]] = {}
    for p in payments:
        if not p.is_debit and p.pairable:
            credits_by_cents.setdefault(p.cents, []).append(p)
    found = []
    for debit in payments:
        if not debit.is_debit or not debit.pairable:
            continue
        for credit in credits_by_cents.get(debit.cents, ()):
            if _fits(debit, credit) and (min(debit.id, credit.id), max(debit.id, credit.id)) not in unpaired:
                found.append(Candidate(debit, credit))
    return found


def _has_self_word(description: str) -> bool:
    return _SELF_WORD_RE.search(description) is not None


def _names_account(description: str, account_ref: str | None) -> bool:
    """Whether the line shows this account's number: its last digits, or more, as
    the line prints them (masked characters and spaces ignored). Only a digit run
    that is the end of the number counts, so a long UPI reference that happens to
    finish in the same four digits does not."""
    digits = re.sub(r"\D", "", account_ref or "")
    if len(digits) < _MIN_NUMBER_DIGITS:
        return False
    return any(
        len(run) <= len(digits) and digits.endswith(run) for run in _DIGIT_RUN.findall(description)
    )


class _Signs:
    """Whether either side of a pair says it is the user's own money."""

    def __init__(self, conn: sqlite3.Connection):
        self._refs = {r["id"]: r["account_ref"] for r in conn.execute("SELECT id, account_ref FROM accounts")}
        self._self_tokens = [
            (r["upi"] or "").strip().lower()
            for r in conn.execute("SELECT upi, relationship FROM people")
            if (r["relationship"] or "").strip().lower() == SELF_RELATIONSHIP and (r["upi"] or "").strip()
        ]

    def _is_self_person(self, p: Payment) -> bool:
        desc = p.description.lower()
        vpa = _txn_vpa(p.upi_meta)
        return any(person_token_matches(token, vpa, p.counterparty_key, desc) for token in self._self_tokens)

    def _side(self, p: Payment, other: Payment) -> bool:
        return (
            _names_account(p.description, self._refs.get(other.account_id))
            or p.category == SELF_CATEGORY
            or self._is_self_person(p)
            or _has_self_word(p.description)
        )

    def of(self, c: Candidate) -> bool:
        return self._side(c.debit, c.credit) or self._side(c.credit, c.debit)


def _has_two_accounts(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] >= 2


def pair_transfers_locked(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """pair_transfers for a caller that already holds the write lock. Returns the
    (debit id, credit id) pairs made. The caller commits."""
    if not _has_two_accounts(conn):
        return []
    candidates = _candidates(conn, _load(conn))
    by_debit: dict[str, list[Candidate]] = {}
    by_credit: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_debit.setdefault(c.debit.id, []).append(c)
        by_credit.setdefault(c.credit.id, []).append(c)
    signs = _Signs(conn)
    made = []
    for c in candidates:
        if len(by_debit[c.debit.id]) == 1 and len(by_credit[c.credit.id]) == 1 and signs.of(c):
            link_pair(conn, c.debit.id, c.credit.id)
            made.append((c.debit.id, c.credit.id))
    return made


def pair_transfers(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Pair every unambiguous, signed transfer between the user's accounts, over
    the whole ledger. Idempotent. Needs two accounts to do anything. Takes the
    write lock and commits. Returns the (debit id, credit id) pairs made."""
    if not _has_two_accounts(conn):
        return []
    with write_transaction(conn):
        return pair_transfers_locked(conn)


def check_can_pair(conn: sqlite3.Connection, transaction_id: str, other_id: str) -> tuple[str, str] | None:
    """(debit id, credit id) when these two payments fit the rule, else None. A sign
    is not needed and neither is being unambiguous: the user choosing the pair is
    the decision (so a pair they once took apart can be made again)."""
    if transaction_id == other_id:
        return None
    payments = _load(conn, ids=[transaction_id, other_id])
    credits = [p for p in payments if not p.is_debit and p.pairable]
    for debit in (p for p in payments if p.is_debit and p.pairable):
        for credit in credits:
            if _fits(debit, credit):
                return debit.id, credit.id
    return None


def transfer_candidates(conn: sqlite3.Connection, transaction_ids: list[str]) -> dict[str, list[dict]]:
    """For Teach Me: the payments each of these could be paired with, closest date
    first. {transaction_id: [{transaction_id, txn_date, amount, direction,
    account_label, gap_days, matches}]}; `matches` is how many candidates the
    payment has in all, so the card can say it is one of several. Only payments
    that have one appear."""
    ids = list(dict.fromkeys(transaction_ids))
    if not ids or not _has_two_accounts(conn):
        return {}
    cents = {
        _cents(r["amount"])
        for start in range(0, len(ids), 400)
        for r in conn.execute(
            f"SELECT amount FROM transactions WHERE id IN ({','.join('?' * len(ids[start:start + 400]))})",
            ids[start:start + 400],
        )
    }
    wanted = set(ids)
    labels = account_labels(conn)
    account_names = {
        r["id"]: label_for_statement(labels, {"account_id": r["id"], "bank_name": r["bank_name"], "account_ref": r["account_ref"]})
        for r in conn.execute("SELECT id, bank_name, account_ref FROM accounts")
    }
    out: dict[str, list[dict]] = {}
    for c in _candidates(conn, _load(conn, cents=cents)):
        for me, other, direction in ((c.debit, c.credit, "to"), (c.credit, c.debit, "from")):
            if me.id in wanted:
                out.setdefault(me.id, []).append({
                    "transaction_id": other.id,
                    "txn_date": other.day.isoformat(),
                    "amount": other.amount,
                    "direction": direction,
                    "account_label": account_names[other.account_id],
                    "gap_days": c.gap_days,
                })
    for found in out.values():
        found.sort(key=lambda f: (f["gap_days"], f["txn_date"], f["transaction_id"]))
        for f in found:
            f["matches"] = len(found)
    return out
