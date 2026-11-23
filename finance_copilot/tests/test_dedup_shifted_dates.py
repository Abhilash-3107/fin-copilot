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


MONTH = ("2026-05-01", "2026-05-31")


def _p(day=1, amount=100.0, dc="debit", balance=900.0, text="UPI/X/1/UPI", span=MONTH):
    """A payment of May 2026. `span` is what its statement covers (it matters for a
    stored payment only)."""
    return Payment.of(date(2026, 5, day).isoformat(), amount, dc, balance, text, span)


def _in_a_wide_file(stored, incoming):
    """What match_payments says for `incoming`, when its file also prints a payment
    on the 1st and on the 31st (so the file covers every day in between) and the
    stored payments' statements cover the whole month."""
    bookends = [_p(day=1, amount=1.0, balance=1.0, text="FIRST"), _p(day=31, amount=2.0, balance=2.0, text="LAST")]
    answer = match_payments(stored, [bookends[0], *incoming, bookends[1]])
    assert answer[0] is None and answer[-1] is None
    return answer[1:-1]


class TestThirdPass:
    def test_an_equal_balance_a_few_days_apart_is_the_same_payment(self):
        assert _in_a_wide_file([_p(day=10, text="A")], [_p(day=13, text="B")]) == [0]
        assert _in_a_wide_file([_p(day=13, text="A")], [_p(day=10, text="B")]) == [0]

    def test_four_days_apart_is_two_payments(self):
        assert _in_a_wide_file([_p(day=10)], [_p(day=14)]) == [None]
        assert SHIFT_DAYS == 3

    def test_the_balance_must_be_equal_and_present_on_both_sides(self):
        assert _in_a_wide_file([_p(day=10, balance=900.0)], [_p(day=11, balance=800.0)]) == [None]
        assert _in_a_wide_file([_p(day=10, balance=None)], [_p(day=11, balance=900.0)]) == [None]
        assert _in_a_wide_file([_p(day=10, balance=900.0)], [_p(day=11, balance=None)]) == [None]

    def test_amount_and_direction_must_be_equal(self):
        assert _in_a_wide_file([_p(day=10)], [_p(day=11, amount=100.01)]) == [None]
        assert _in_a_wide_file([_p(day=10)], [_p(day=11, dc="credit")]) == [None]

    def test_the_nearest_day_is_taken_first_and_a_stored_payment_once(self):
        stored = [_p(day=10), _p(day=12)]
        assert _in_a_wide_file(stored, [_p(day=13)]) == [1]
        assert _in_a_wide_file([_p(day=10)], [_p(day=11), _p(day=12)]) == [0, None]

    def test_the_first_two_passes_take_what_they_can_before_it_looks_further(self):
        stored = [_p(day=11, text="A")]
        incoming = [_p(day=10, text="B"), _p(day=11, text="C")]
        # Same day and balance is the first pass's: it goes to the payment on that day.
        assert _in_a_wide_file(stored, incoming) == [None, 0]

    def test_a_stored_payment_outside_the_days_the_new_file_prints_is_not_shifted_onto(self):
        # The new file runs 4..10 May and the stored payment is on the 2nd: the new
        # file would have printed it, so a payment of the same figures is another one.
        stored = [_p(day=2, text="A")]
        incoming = [_p(day=4, text="B"), _p(day=10, text="C", balance=500.0)]
        assert match_payments(stored, incoming) == [None, None]
        # Inside those days it is the same payment.
        assert match_payments([_p(day=5, text="A")], incoming) == [0, None]

    def test_a_new_payment_outside_the_days_the_stored_statement_covers_is_not_shifted(self):
        # The stored statement covers 1..14 May and the new payment is on the 16th:
        # that statement would have printed it if it were there.
        stored = [_p(day=14, text="A", span=("2026-05-01", "2026-05-14"))]
        incoming = [_p(day=10, text="C", amount=7.0, balance=1.0), _p(day=16, text="B")]
        assert match_payments(stored, incoming) == [None, None]
        stored = [_p(day=14, text="A", span=("2026-05-01", "2026-05-16"))]
        assert match_payments(stored, incoming) == [None, 0]

    def test_a_stored_payment_with_no_known_span_is_never_shifted_onto(self):
        assert _in_a_wide_file([_p(day=10, span=None)], [_p(day=11)]) == [None]

    def test_shift_days_zero_is_the_first_two_passes_alone(self):
        assert match_payments([_p(day=10)], [_p(day=11)], shift_days=0) == [None]


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


HEAD = "Example Bank\nAccount Number,XXXX4321\n\nDate,Narration,Withdrawal Amt.,Deposit Amt.,Balance\n"
# A boundary: file one ends on a +100, file two starts with a -100 that is undone by a
# +100, so the balance after its second row is the one file one ended on.
FILE_ONE = HEAD + "\n".join([
    "10/10/2026,UPI/GROCER/1,500.00,,9500.00",
    "12/10/2026,UPI/CAFE/2,400.00,,9100.00",
    "14/10/2026,UPI/RAVI/3/SPLIT,,100.00,9200.00",
])
FILE_TWO = HEAD + "\n".join([
    "15/10/2026,UPI/SHOP/4/FAILED,100.00,,9100.00",
    "16/10/2026,UPI/SHOP/4/REVERSAL,,100.00,9200.00",
    "20/10/2026,UPI/RENT/5,5000.00,,4200.00",
])


def _ledger_ends_at(conn, opening):
    balance = opening
    for r in conn.execute("SELECT debit_credit, amount FROM transactions"):
        balance += r["amount"] if r["debit_credit"] == "credit" else -r["amount"]
    return round(balance, 2)


class TestAReversalAtAFileBoundary:
    def test_the_next_file_keeps_the_reversal_and_the_ledger_reconciles(self):
        from finance_copilot.pipeline.spreadsheet_ingest import ingest_spreadsheet

        conn = _conn()
        ingest_spreadsheet(conn, FILE_ONE.encode(), new_account_name="Main")
        second = ingest_spreadsheet(conn, FILE_TWO.encode())
        assert (second.rows_in_file, second.rows_added) == (3, 3)
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 6
        assert _ledger_ends_at(conn, 10000.0) == 4200.0

    def test_a_file_that_prints_a_day_a_few_days_off_still_matches_inside_both_spans(self):
        # A value date and a transaction date: the second file starts earlier and
        # prints the first two payments a day later; both files cover both days.
        from finance_copilot.pipeline.spreadsheet_ingest import ingest_spreadsheet

        conn = _conn()
        ingest_spreadsheet(conn, FILE_ONE.encode(), new_account_name="Main")
        shifted = HEAD + "\n".join([
            "08/10/2026,UPI/SALARY/9,500.00,,10000.00",
            "11/10/2026,UPI/GROCER/1,500.00,,9500.00",
            "13/10/2026,UPI/CAFE/2,400.00,,9100.00",
            "14/10/2026,UPI/RAVI/3/SPLIT,,100.00,9200.00",
        ])
        got = ingest_spreadsheet(conn, shifted.encode())
        assert (got.rows_in_file, got.rows_added) == (4, 1)
