"""Which payments of a new file the account already has.

An account's payments belong to the statement that brought them first. A later
file that covers some of the same days adds only the payments that are new; the
rest are "already in". This module decides which is which, the same way for a
PDF, a spreadsheet and a statement moved into an account.

The rule (within one account). A payment of the file matches a stored payment
when the date, the amount (to the paisa) and the direction are equal, and
- both carry a running balance and the balances are equal (to the paisa): the
  descriptions may differ, a CSV export prints the line another way than the
  PDF; or
- at least one has no running balance, and the descriptions are equal once
  upper-cased with whitespace collapsed.
Counting is a multiset: three identical payments in the file against two in
the ledger leave one new. A stored payment is never matched twice. Two payments
with equal text and different balances are two payments, so the balance, when
there is one, decides.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from finance_copilot.models.transaction import Transaction


def _paisa(value: float) -> int:
    return round(value * 100)


def _normalised(text: str) -> str:
    return " ".join(text.upper().split())


@dataclass(frozen=True)
class Payment:
    """The parts of a payment that decide whether two are the same one."""

    day: str  # ISO date
    paisa: int
    direction: str  # 'debit' | 'credit'
    balance: int | None  # running balance in paise
    text: str  # normalised description

    @classmethod
    def of(
        cls, day: str, amount: float, direction: str, balance: float | None, description: str
    ) -> Payment:
        return cls(
            day, _paisa(amount), direction,
            None if balance is None else _paisa(balance), _normalised(description),
        )

    @classmethod
    def from_transaction(cls, txn: Transaction) -> Payment:
        return cls.of(
            txn.txn_date.isoformat(), txn.amount, txn.debit_credit,
            txn.running_balance, txn.raw_description,
        )

    def _slot(self) -> tuple[str, int, str]:
        return (self.day, self.paisa, self.direction)


def match_payments(stored: Sequence[Payment], incoming: Sequence[Payment]) -> list[int | None]:
    """For each incoming payment, the index of the stored payment it is, or None
    when it is new. A stored payment answers for at most one incoming payment.

    Matches on equal balances are taken first, then matches on the description,
    taking payments that carry a balance before those that do not (they can
    match fewer stored payments, so they must not lose theirs to one that could
    have matched another). Within those, earlier stored payments are taken first,
    so the answer does not depend on anything but the order of the two lists.
    """
    slots: dict[tuple[str, int, str], list[int]] = {}
    for index, payment in enumerate(stored):
        slots.setdefault(payment._slot(), []).append(index)

    taken: set[int] = set()
    answer: list[int | None] = [None] * len(incoming)

    def take(i: int, accepts: Callable[[Payment, Payment], bool]) -> None:
        for j in slots.get(incoming[i]._slot(), ()):
            if j not in taken and accepts(stored[j], incoming[i]):
                taken.add(j)
                answer[i] = j
                return

    def same_balance(have: Payment, new: Payment) -> bool:
        return have.balance is not None and have.balance == new.balance

    def same_text(have: Payment, new: Payment) -> bool:
        return (have.balance is None or new.balance is None) and have.text == new.text

    for i, payment in enumerate(incoming):
        if payment.balance is not None:
            take(i, same_balance)
    for with_balance in (True, False):
        for i, payment in enumerate(incoming):
            if answer[i] is None and (payment.balance is not None) == with_balance:
                take(i, same_text)
    return answer


# What the account holds, read for the days the incoming payments fall on.
_STORED_SQL = """
SELECT t.id, t.txn_date, t.amount, t.debit_credit, t.running_balance, t.raw_description
FROM transactions t
JOIN statements s ON s.id = t.statement_id
WHERE s.account_id = ? AND t.txn_date BETWEEN ? AND ?
ORDER BY t.rowid
"""


def _payment_of_row(row: sqlite3.Row) -> Payment:
    return Payment.of(
        row["txn_date"], row["amount"], row["debit_credit"], row["running_balance"], row["raw_description"]
    )


def _stored_in_account(
    conn: sqlite3.Connection, account_id: str, first_day: str, last_day: str
) -> tuple[list[Payment], list[str]]:
    """The account's payments over those days, and their ids in the same order."""
    rows = conn.execute(_STORED_SQL, (account_id, first_day, last_day)).fetchall()
    return [_payment_of_row(r) for r in rows], [r["id"] for r in rows]


@dataclass
class RowSplit:
    """A file's payments, sorted into the ones the account lacks and the ones it has."""

    new: list[Transaction]
    already_in: int
    # The file covers dates the account has statements for, yet none of its
    # payments there matched anything: it is probably another account's.
    overlaps_without_match: bool


def split_new_rows(
    conn: sqlite3.Connection, account_id: str, rows: Sequence[Transaction]
) -> RowSplit:
    """Split a parsed file's payments (in chain order) against what `account_id`
    already holds. Reads only; the caller inserts `new`. Call it inside the
    transaction that inserts, so two files arriving together see each other."""
    if not rows:
        return RowSplit([], 0, False)
    first = min(t.txn_date for t in rows).isoformat()
    last = max(t.txn_date for t in rows).isoformat()
    stored, _ = _stored_in_account(conn, account_id, first, last)
    matches = match_payments(stored, [Payment.from_transaction(t) for t in rows])
    new = [t for t, m in zip(rows, matches, strict=True) if m is None]
    already_in = len(rows) - len(new)

    covered = conn.execute(
        "SELECT period_start, period_end FROM statements "
        "WHERE account_id = ? AND period_start IS NOT NULL AND period_end IS NOT NULL",
        (account_id,),
    ).fetchall()
    in_overlap = any(
        any(c["period_start"] <= t.txn_date.isoformat() <= c["period_end"] for c in covered)
        for t in rows
    )
    return RowSplit(new, already_in, in_overlap and already_in == 0)


def payments_account_already_has(
    conn: sqlite3.Connection, statement_id: str, account_id: str
) -> list[tuple[str, str]]:
    """(duplicate id, survivor id) for each of a statement's payments that
    `account_id` holds already (in other statements), for a statement moved into
    that account. The account's own copy, the survivor, stays; the statement's is
    the duplicate."""
    mine = conn.execute(
        "SELECT id, txn_date, amount, debit_credit, running_balance, raw_description "
        "FROM transactions WHERE statement_id = ? ORDER BY rowid",
        (statement_id,),
    ).fetchall()
    if not mine:
        return []
    days = [r["txn_date"] for r in mine]
    stored, stored_ids = _stored_in_account(conn, account_id, min(days), max(days))
    matches = match_payments(stored, [_payment_of_row(r) for r in mine])
    return [(r["id"], stored_ids[m]) for r, m in zip(mine, matches, strict=True) if m is not None]
