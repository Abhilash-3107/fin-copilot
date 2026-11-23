"""The matcher's third pass: equal balance, amount and direction, dates up to 3 days apart.

It applies to every source, so besides its own branches these tests prove that it
changes nothing for the PDF fixtures: every order of ingesting them leaves the
same payments with the pass on as with it off (shift_days=0).
"""
from __future__ import annotations

import itertools
import sqlite3
from datetime import date
from pathlib import Path

from finance_copilot.db.connection import init_db
from finance_copilot.pipeline.dedup import SHIFT_DAYS, Payment, match_payments
from finance_copilot.pipeline.ingest import NothingNewError, ingest_pdf

FIXTURES = Path(__file__).parent / "fixtures"
PDFS = ["sample_statement.pdf", "sample_statement_nov.pdf", "sample_statement_overlap.pdf"]


def _p(day=1, amount=100.0, dc="debit", balance=900.0, text="UPI/X/1/UPI"):
    return Payment.of(date(2026, 5, day).isoformat(), amount, dc, balance, text)


class TestThirdPass:
    def test_an_equal_balance_a_few_days_apart_is_the_same_payment(self):
        assert match_payments([_p(day=1, text="A")], [_p(day=4, text="B")]) == [0]
        assert match_payments([_p(day=4, text="A")], [_p(day=1, text="B")]) == [0]

    def test_four_days_apart_is_two_payments(self):
        assert match_payments([_p(day=1)], [_p(day=5)]) == [None]
        assert SHIFT_DAYS == 3

    def test_the_balance_must_be_equal_and_present_on_both_sides(self):
        assert match_payments([_p(day=1, balance=900.0)], [_p(day=2, balance=800.0)]) == [None]
        assert match_payments([_p(day=1, balance=None)], [_p(day=2, balance=900.0)]) == [None]
        assert match_payments([_p(day=1, balance=900.0)], [_p(day=2, balance=None)]) == [None]

    def test_amount_and_direction_must_be_equal(self):
        assert match_payments([_p(day=1)], [_p(day=2, amount=100.01)]) == [None]
        assert match_payments([_p(day=1)], [_p(day=2, dc="credit")]) == [None]

    def test_the_nearest_day_is_taken_first_and_a_stored_payment_once(self):
        stored = [_p(day=1), _p(day=3)]
        assert match_payments(stored, [_p(day=4)]) == [1]
        assert match_payments([_p(day=1)], [_p(day=2), _p(day=3)]) == [0, None]

    def test_the_first_two_passes_take_what_they_can_before_it_looks_further(self):
        stored = [_p(day=2, text="A")]
        incoming = [_p(day=1, text="B"), _p(day=2, text="C")]
        # Same day and balance is the first pass's: it goes to the payment on that day.
        assert match_payments(stored, incoming) == [None, 0]

    def test_shift_days_zero_is_the_first_two_passes_alone(self):
        assert match_payments([_p(day=1)], [_p(day=2)], shift_days=0) == [None]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def _ledger_after(order, shift_days, monkeypatch):
    import finance_copilot.pipeline.dedup as dedup

    original = dedup.match_payments
    monkeypatch.setattr(
        dedup, "match_payments", lambda s, i, shift_days=shift_days: original(s, i, shift_days)
    )
    conn = _conn()
    added = []
    for name in order:
        try:
            added.append(ingest_pdf(str(FIXTURES / name), conn=conn).rows_added)
        except NothingNewError:
            added.append(0)
    rows = [
        tuple(r) for r in conn.execute(
            "SELECT txn_date, amount, debit_credit, running_balance, raw_description "
            "FROM transactions ORDER BY txn_date, amount, raw_description"
        )
    ]
    return added, rows


class TestPdfFixturesAreUnchanged:
    def test_every_order_of_the_overlapping_fixtures_gives_the_same_ledger_with_the_pass_off_and_on(
        self, monkeypatch
    ):
        for order in itertools.permutations(PDFS):
            off = _ledger_after(order, 0, monkeypatch)
            on = _ledger_after(order, SHIFT_DAYS, monkeypatch)
            assert on == off, order
            assert len(on[1]) == 8 + 8 + 3, order  # October, November and the three after it
