"""Pairing: a transfer between your own accounts is not spending.

A debit in one account and a credit in another are a pair when
  - the accounts are different (both are the user's: an account exists only
    because the user added a statement of it),
  - the amounts are equal to the paisa,
  - the dates are at most WINDOW_DAYS apart (NEFT and IMPS can land a day or two
    later, a weekend adds one),
  - neither payment is already in a transfer link, and the user has not taken this
    pair apart before,
  - neither carries a label the user made or wrote a rule for (manual, imported,
    user_rule) in a category other than Self Transfers (one they sorted as Self
    Transfers may pair).

Those are the candidates, defined once, in SQL, by db/queries/transfers.fits_sql:
the finder below and "needs you" in db/queries/common.unsorted_sql both read it, so
a payment Teach Me is asked about because of a candidate always has one here. A pair is made by itself only when it is unambiguous
(the debit has exactly one candidate credit and that credit exactly one candidate
debit) AND there is a sign it is the user's own money, because amount and date
alone also match a friend paid and a stranger paying the same sum in:
  - the other account's number appears in the line (its full stored digits, or its
    last four right after a mask character or an account word: not a bare run),
  - a side is already sorted Self Transfers, by anyone,
  - a side's payee is a person marked "self" in People,
  - the line has a self-transfer word (SELF_TRANSFER_WORDS), outside a UPI handle,
    and not SELF on a cash withdrawal (that is cash taken out, not a transfer).
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
    SELF_CATEGORY,
    candidate_pairs,
    link_pair,
)

# Words that say a line moves money between the user's own accounts, matched
# case-insensitively and only as whole tokens ("OWN" in "TO OWN SBI SAVINGS" but
# not in "DOWNTOWN" or "GROWN"; an underscore is part of a word, so OWN_SHOP is not
# OWN). The phrases are the plain ways banks and people write it; a bare OWN or
# SELF already covers them, they are listed so the list reads as what it means.
# Keep it short: a word here lets a pair be made without asking, so it must be one
# that hardly ever appears on a payment to someone else.
SELF_TRANSFER_WORDS = ("own", "self", "own account", "to self", "from self")

# "CASH WDL CHQ 000123 SELF" is a cheque to self, cash taken out: the word SELF on
# a line with one of these tokens is not a transfer.
_CASH_WORDS = ("cash", "atm", "wdl")
# A UPI handle (OWN_SHOP@ybl): nothing inside it is a word. Handles use letters,
# digits, dots, hyphens and underscores; a slash ends one.
_UPI_HANDLE_RE = re.compile(r"[\w.-]*@[\w.-]+")


def _words_re(words: tuple[str, ...]) -> re.Pattern:
    body = "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in words)
    return re.compile(rf"(?<![a-z0-9_])(?:{body})(?![a-z0-9_])", re.IGNORECASE)


_ANY_WORD_RE = _words_re(SELF_TRANSFER_WORDS)
_OWN_WORD_RE = _words_re(tuple(w for w in SELF_TRANSFER_WORDS if "self" not in w))
_CASH_RE = _words_re(_CASH_WORDS)
_DIGIT_RUN = re.compile(r"\d{4,}")
_MIN_NUMBER_DIGITS = 6
_LAST_DIGITS = 4
# The last four digits of an account count only right after a mask character or
# an account word: "XX2222", "A/C 2222", "AC-2222", "ACCOUNT: XX2222". A mask is a
# whole run of X or * that starts a word, so the XX inside "TAXX2222" is not one.
_MASKED_LAST4_RE = re.compile(
    r"(?:(?<![a-z0-9])[x*]+[\s-]?|(?<![a-z0-9])a/?c(?:count)?[\s.:/#-]*(?:[x*]+[\s-]?)?)(\d{4})(?!\d)",
    re.IGNORECASE,
)


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


def _load(conn: sqlite3.Connection, ids: list[str]) -> dict[str, Payment]:
    """These payments as pairing sees them."""
    out: dict[str, Payment] = {}
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        for r in conn.execute(
            f"""
            SELECT t.id, t.txn_date, t.amount, t.debit_credit, s.account_id, t.raw_description,
                   t.counterparty_key, t.upi_meta, a.source, a.category
            FROM transactions t
            JOIN statements s ON s.id = t.statement_id
            LEFT JOIN annotations a ON a.transaction_id = t.id
            WHERE t.id IN ({','.join('?' * len(chunk))})
            """,
            chunk,
        ):
            out[r["id"]] = Payment(
                id=r["id"], day=date.fromisoformat(r["txn_date"][:10]), amount=r["amount"],
                cents=_cents(r["amount"]), is_debit=r["debit_credit"] == "debit", account_id=r["account_id"],
                description=r["raw_description"] or "", counterparty_key=r["counterparty_key"],
                upi_meta=r["upi_meta"], source=r["source"], category=r["category"],
            )
    return out


def _candidates(
    conn: sqlite3.Connection, *, ids: list[str] | None = None, between: tuple[str, str] | None = None,
    respect_unpaired: bool = True,
) -> list[Candidate]:
    """Every debit and credit that fit the rule (transfers.fits_sql), closest first."""
    pairs = candidate_pairs(conn, ids=ids, between=between, respect_unpaired=respect_unpaired)
    payments = _load(conn, sorted({i for d, c, _ in pairs for i in (d, c)}))
    return [Candidate(payments[d], payments[c]) for d, c, _ in pairs]


def _has_self_word(description: str) -> bool:
    """A self-transfer word in the line, outside any UPI handle. On a cash withdrawal
    line (CASH, ATM or WDL as a token) the word SELF does not count, only the OWN
    words."""
    text = _UPI_HANDLE_RE.sub(" ", description)
    pattern = _OWN_WORD_RE if _CASH_RE.search(text) else _ANY_WORD_RE
    return pattern.search(text) is not None


def _names_account(description: str, account_ref: str | None) -> bool:
    """Whether the line shows this account's number: all of its stored digits (six or
    more), or its last four right after a mask character (X, x, *) or an account word
    (A/C, AC, ACCOUNT, with optional punctuation or space). A bare four-digit run is
    not enough ("CHQ NO 3521"), and a longer run that merely ends in the same digits
    (a UPI reference) is not the number."""
    digits = re.sub(r"\D", "", account_ref or "")
    if len(digits) < _LAST_DIGITS:
        return False
    if len(digits) >= _MIN_NUMBER_DIGITS and any(run == digits for run in _DIGIT_RUN.findall(description)):
        return True
    return any(m.group(1) == digits[-_LAST_DIGITS:] for m in _MASKED_LAST4_RE.finditer(description))


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
    candidates = _candidates(conn)
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
    found = _candidates(conn, between=(transaction_id, other_id), respect_unpaired=False)
    return (found[0].debit.id, found[0].credit.id) if found else None


def transfer_candidates(conn: sqlite3.Connection, transaction_ids: list[str]) -> dict[str, list[dict]]:
    """For Teach Me: the payments each of these could be paired with, closest date
    first. {transaction_id: [{transaction_id, txn_date, amount, direction,
    account_label, gap_days, matches}]}; `matches` is how many candidates the
    payment has in all, so the card can say it is one of several. Only payments
    that have one appear."""
    ids = list(dict.fromkeys(transaction_ids))
    if not ids or not _has_two_accounts(conn):
        return {}
    wanted = set(ids)
    labels = account_labels(conn)
    account_names = {
        r["id"]: label_for_statement(labels, {"account_id": r["id"], "bank_name": r["bank_name"], "account_ref": r["account_ref"]})
        for r in conn.execute("SELECT id, bank_name, account_ref FROM accounts")
    }
    out: dict[str, list[dict]] = {}
    for c in _candidates(conn, ids=ids):
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
