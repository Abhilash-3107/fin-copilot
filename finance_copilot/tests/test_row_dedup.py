"""Overlapping statements add only new rows.

The matcher (pipeline/dedup.py) first, as pure functions over payments; then
ingest on the real fixtures and on scripted parsers; then the routes (the upload
response, moving a statement into an account that already holds its rows, the
months the ledger has payments in, what a delete takes with it).
"""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import ulid
from fastapi.testclient import TestClient

from finance_copilot.db.connection import init_db
from finance_copilot.models.transaction import Transaction
from finance_copilot.parsers.base import StatementParser
from finance_copilot.pipeline.dedup import Payment, match_payments
from finance_copilot.pipeline.ingest import (
    DuplicateStatementError,
    NothingNewError,
    ingest_pdf,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


# --- the matcher ------------------------------------------------------------


def _p(day=1, amount=100.0, dc="debit", balance=None, text="UPI/X/1/UPI"):
    return Payment.of(date(2026, 5, day).isoformat(), amount, dc, balance, text)


class TestMatchPayments:
    def test_the_same_balance_matches_though_the_descriptions_differ(self):
        """A CSV export prints the line another way than the PDF does."""
        stored = [_p(balance=900.0, text="UPI/SWIGGY/ORDER/1/UPI")]
        incoming = [_p(balance=900.0, text="Swiggy order 1")]
        assert match_payments(stored, incoming) == [0]

    def test_different_balances_do_not_match_though_the_descriptions_are_equal(self):
        """Two identical payments on one day are two payments: the balance tells them apart."""
        stored = [_p(balance=900.0)]
        incoming = [_p(balance=800.0)]
        assert match_payments(stored, incoming) == [None]

    def test_no_balance_and_equal_descriptions_match(self):
        assert match_payments([_p(text="UPI/X/1/UPI")], [_p(text="UPI/X/1/UPI")]) == [0]

    def test_no_balance_and_different_descriptions_are_both_kept(self):
        assert match_payments([_p(text="UPI/X/1/UPI")], [_p(text="UPI/Y/1/UPI")]) == [None]

    def test_one_side_without_a_balance_falls_back_to_the_description(self):
        assert match_payments([_p(balance=900.0, text="A")], [_p(balance=None, text="A")]) == [0]
        assert match_payments([_p(balance=None, text="A")], [_p(balance=900.0, text="A")]) == [0]
        assert match_payments([_p(balance=900.0, text="A")], [_p(balance=None, text="B")]) == [None]

    def test_descriptions_are_compared_in_upper_case_with_spaces_collapsed(self):
        stored = [_p(text="  upi/swiggy   order ")]
        assert match_payments(stored, [_p(text="UPI/SWIGGY ORDER")]) == [0]

    def test_a_different_day_amount_or_direction_never_matches(self):
        stored = [_p(day=1, amount=100.0, dc="debit", balance=900.0)]
        assert match_payments(stored, [_p(day=2, amount=100.0, dc="debit", balance=900.0)]) == [None]
        assert match_payments(stored, [_p(day=1, amount=100.01, dc="debit", balance=900.0)]) == [None]
        assert match_payments(stored, [_p(day=1, amount=100.0, dc="credit", balance=900.0)]) == [None]

    def test_amounts_and_balances_are_compared_to_the_paisa(self):
        stored = [_p(amount=100.10, balance=899.9)]
        # 0.1 + 0.2 style float noise is not a different amount.
        assert match_payments(stored, [_p(amount=100.1000000001, balance=899.9000000001)]) == [0]

    def test_counting_is_a_multiset(self):
        """Three identical rows in the file, two in the ledger: one is new."""
        stored = [_p(text="A"), _p(text="A")]
        incoming = [_p(text="A"), _p(text="A"), _p(text="A")]
        matched = match_payments(stored, incoming)
        assert sorted(m for m in matched if m is not None) == [0, 1]
        assert matched.count(None) == 1

    def test_a_stored_row_is_never_matched_twice(self):
        matched = match_payments([_p(balance=900.0)], [_p(balance=900.0), _p(balance=900.0)])
        assert matched == [0, None]

    def test_two_payments_a_day_apart_by_balance_each_find_their_own(self):
        stored = [_p(balance=900.0), _p(balance=800.0)]
        incoming = [_p(balance=800.0), _p(balance=900.0)]
        assert match_payments(stored, incoming) == [1, 0]

    def test_balance_matches_are_taken_before_description_matches(self):
        """A row that can only match on its balance is not robbed of it by a
        description match, whatever order the rows come in."""
        stored = [_p(balance=None, text="A"), _p(balance=900.0, text="A")]
        incoming = [_p(balance=None, text="A"), _p(balance=900.0, text="A")]
        assert sorted(match_payments(stored, incoming)) == [0, 1]
        assert None not in match_payments(stored, incoming)
        assert None not in match_payments(stored, list(reversed(incoming)))

    def test_a_row_with_a_balance_is_matched_before_one_without(self):
        """Stored: no balance; incoming: one with a balance, one without, same
        description. Both cannot match the one stored row, but the one that
        carries a balance has fewer ways to match, so it must not be the loser."""
        stored = [_p(balance=None, text="A"), _p(balance=700.0, text="A")]
        incoming = [_p(balance=None, text="A"), _p(balance=900.0, text="A")]
        matched = match_payments(stored, incoming)
        assert matched[1] == 0  # 900 can only match the stored row with no balance
        assert matched[0] == 1  # the one without a balance takes the other

    def test_nothing_stored_matches_nothing(self):
        assert match_payments([], [_p(), _p()]) == [None, None]

    def test_nothing_incoming_is_an_empty_answer(self):
        assert match_payments([_p()], []) == []


# --- ingest -----------------------------------------------------------------


class _FakeParser(StatementParser):
    version = "1"

    def __init__(self, rows, bank="kotak", ref="5551111", period=None):
        self.bank_name = bank
        self._ref = ref
        self._rows = rows
        self._period = period

    def detect(self, path, password=None):
        return True

    def parse(self, path, password=None):
        return [t.model_copy(update={"id": str(ulid.ULID())}) for t in self._rows]

    def extract_account_ref(self, path, password=None):
        return self._ref

    def extract_period(self, path, password=None):
        return self._period


def _t(day, amount=100.0, dc="debit", balance=None, text=None, month=5):
    return Transaction(
        txn_date=date(2026, month, day), amount=amount, debit_credit=dc,
        raw_description=text or f"UPI/X/{day}/UPI", running_balance=balance,
    )


def _chain(start, days, month=5):
    """One 100 rupee debit on each of `days`, with running balances from `start`."""
    out, balance = [], start
    for day in days:
        balance -= 100
        out.append(_t(day, balance=float(balance), month=month))
    return out


# One account's May as a single chain. A statement is some of its days: the rows
# and balances are the bank's, so two statements that share a day share its rows.
MAY = _chain(10000, range(1, 13))


def _days(*days):
    return [t for t in MAY if t.txn_date.day in days]


def _ingest(conn, parser, tag):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"%PDF " + tag.encode())
        path = tmp.name
    try:
        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=parser):
            return ingest_pdf(path, conn=conn)
    finally:
        Path(path).unlink()


def _count(conn, statement_id=None):
    if statement_id:
        return conn.execute("SELECT COUNT(*) FROM transactions WHERE statement_id = ?", (statement_id,)).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]


class TestOverlappingFixtures:
    def _fixture(self, conn, name):
        return ingest_pdf(str(FIXTURES / name), conn=conn)

    def test_the_overlap_after_both_months_adds_only_what_is_new(self):
        conn = _make_conn()
        self._fixture(conn, "sample_statement.pdf")
        self._fixture(conn, "sample_statement_nov.pdf")
        overlap = self._fixture(conn, "sample_statement_overlap.pdf")
        assert (overlap.rows_in_file, overlap.rows_added) == (15, 3)
        assert _count(conn, overlap.id) == 3
        assert _count(conn) == 8 + 8 + 3
        # No payment appears twice.
        dupes = conn.execute(
            "SELECT txn_date, amount, raw_description, COUNT(*) FROM transactions "
            "GROUP BY txn_date, amount, raw_description HAVING COUNT(*) > 1"
        ).fetchall()
        assert dupes == []

    def test_the_overlap_first_then_the_months_adds_what_each_month_has_that_it_lacks(self):
        conn = _make_conn()
        overlap = self._fixture(conn, "sample_statement_overlap.pdf")
        assert (overlap.rows_in_file, overlap.rows_added) == (15, 15)
        october = self._fixture(conn, "sample_statement.pdf")
        # The 5th to the 8th are in; the 1st to the 4th are new.
        assert (october.rows_in_file, october.rows_added) == (8, 4)
        assert {r["txn_date"] for r in conn.execute("SELECT txn_date FROM transactions WHERE statement_id = ?", (october.id,))} == {
            "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
        }
        with pytest.raises(NothingNewError) as nothing:
            self._fixture(conn, "sample_statement_nov.pdf")
        assert "all 8 payments in this file are already in Bank ··0000" in str(nothing.value)
        assert _count(conn) == 15 + 4
        assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 2

    def test_the_overlap_between_the_months_brings_in_the_payments_after_them(self):
        conn = _make_conn()
        self._fixture(conn, "sample_statement.pdf")
        overlap = self._fixture(conn, "sample_statement_overlap.pdf")
        # October's 5th to 8th match; November's eight and the three after are new.
        assert (overlap.rows_in_file, overlap.rows_added) == (15, 11)
        assert overlap.notices == []

    def test_a_statement_records_its_whole_file_apart_from_what_it_added(self):
        conn = _make_conn()
        self._fixture(conn, "sample_statement.pdf")
        self._fixture(conn, "sample_statement_nov.pdf")
        overlap = self._fixture(conn, "sample_statement_overlap.pdf")
        row = conn.execute("SELECT * FROM statements WHERE id = ?", (overlap.id,)).fetchone()
        assert (row["rows_in_file"], row["rows_added"]) == (15, 3)
        # The file's own dates and balances, not those of the three rows it added.
        assert (row["period_start"], row["period_end"]) == ("2026-10-05", "2026-11-15")
        assert (row["printed_start"], row["printed_end"]) == ("2026-10-05", "2026-11-15")
        assert (row["opening_balance"], row["closing_balance"]) == (103744.5 + 5000.0, 177504.75)
        # The balance check is the whole file's: 14 steps between 15 balances, all reconciling.
        assert (row["balance_checked"], row["balance_consistent"]) == (14, 14)

    def test_the_same_file_twice_is_still_refused_by_its_hash(self):
        conn = _make_conn()
        self._fixture(conn, "sample_statement.pdf")
        with pytest.raises(DuplicateStatementError) as refused:
            self._fixture(conn, "sample_statement.pdf")
        assert str(refused.value) == "This statement was already added (Bank ··0000, October 2026)"

    def test_a_full_month_after_its_mid_month_part_adds_the_rest(self):
        """A mid-month download first, then the month's statement."""
        conn = _make_conn()
        mid = _ingest(conn, _FakeParser(_days(4, 5, 6)), "mid")
        whole = _ingest(conn, _FakeParser(_days(1, 2, 3, 4, 5, 6, 7, 8)), "whole")
        assert (mid.rows_in_file, mid.rows_added) == (3, 3)
        assert (whole.rows_in_file, whole.rows_added) == (8, 5)
        assert _count(conn) == 8

    def test_a_three_month_file_across_two_existing_months(self):
        conn = _make_conn()
        balance, three = 10000.0, []
        for month in (3, 4, 5):
            for day in (1, 2, 3):
                balance -= 100
                three.append(_t(day, balance=balance, month=month))
        _ingest(conn, _FakeParser(three[0:3]), "march")
        _ingest(conn, _FakeParser(three[6:9]), "may")
        both = _ingest(conn, _FakeParser(three), "three")
        assert (both.rows_in_file, both.rows_added) == (9, 3)
        assert {r["txn_date"][:7] for r in conn.execute("SELECT txn_date FROM transactions WHERE statement_id = ?", (both.id,))} == {"2026-04"}
        assert _count(conn) == 9

    def test_rows_are_inserted_in_chain_order(self):
        conn = _make_conn()
        _ingest(conn, _FakeParser(_days(3, 4)), "later")
        earlier = _ingest(conn, _FakeParser(_days(1, 2, 3, 4)), "earlier")
        rows = [
            (r["txn_date"], r["running_balance"])
            for r in conn.execute("SELECT txn_date, running_balance FROM transactions WHERE statement_id = ? ORDER BY rowid", (earlier.id,))
        ]
        assert rows == [("2026-05-01", 9900.0), ("2026-05-02", 9800.0)]


class TestNothingNew:
    def test_a_file_whose_rows_are_all_in_creates_nothing(self):
        conn = _make_conn()
        _ingest(conn, _FakeParser(_days(1, 2, 3, 4)), "a")
        before = (_count(conn), conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0])
        with pytest.raises(NothingNewError) as nothing:
            _ingest(conn, _FakeParser(_days(2, 3)), "b")
        assert str(nothing.value) == "Nothing new: all 2 payments in this file are already in Kotak ··1111."
        assert (_count(conn), conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0]) == before

    def test_the_message_for_one_payment(self):
        conn = _make_conn()
        _ingest(conn, _FakeParser(_days(1, 2)), "a")
        with pytest.raises(NothingNewError) as nothing:
            _ingest(conn, _FakeParser(_days(1)), "b")
        assert str(nothing.value) == "Nothing new: the one payment in this file is already in Kotak ··1111."

    def test_the_message_names_the_account_by_the_name_the_user_gave_it(self):
        conn = _make_conn()
        first = _ingest(conn, _FakeParser(_days(1, 2)), "a")
        conn.execute("UPDATE accounts SET name = 'Kotak savings' WHERE id = ?", (first.account_id,))
        conn.commit()
        with pytest.raises(NothingNewError, match="already in Kotak savings"):
            _ingest(conn, _FakeParser(_days(1, 2)), "b")

    def test_a_refusal_leaves_no_account_and_does_not_adopt_a_number(self):
        """The account lookup and the refusal are one step: an adopted number is rolled back."""
        conn = _make_conn()
        _ingest(conn, _FakeParser(_days(1, 2), ref=None), "a")
        with pytest.raises(NothingNewError) as nothing:
            _ingest(conn, _FakeParser(_days(1, 2), ref="5551111"), "b")
        # The account is named as it stands now, not by the number the file would have given it.
        assert str(nothing.value) == "Nothing new: all 2 payments in this file are already in Kotak."
        assert conn.execute("SELECT account_ref FROM accounts").fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1


class TestMatchingInAnAccount:
    def test_a_row_in_another_account_is_not_a_match(self):
        conn = _make_conn()
        a = _ingest(conn, _FakeParser(_days(1, 2), ref="1111"), "a")
        b = _ingest(conn, _FakeParser(_days(1, 2), ref="2222"), "b")
        assert a.account_id != b.account_id
        assert b.rows_added == 2
        assert _count(conn) == 4

    def test_a_row_with_the_same_text_and_a_different_balance_is_kept(self):
        conn = _make_conn()
        _ingest(conn, _FakeParser(_chain(10000, [1, 2])), "a")
        other = _ingest(conn, _FakeParser(_chain(20000, [1, 2])), "b")
        assert (other.rows_in_file, other.rows_added) == (2, 2)

    def test_rows_without_a_balance_match_on_the_printed_line(self):
        conn = _make_conn()
        a = [_t(1, text="UPI/A/1/UPI"), _t(2, text="UPI/B/2/UPI")]
        _ingest(conn, _FakeParser(a), "a")
        b = [_t(2, text="UPI/B/2/UPI"), _t(2, text="UPI/C/2/UPI")]
        second = _ingest(conn, _FakeParser(b), "b")
        assert (second.rows_in_file, second.rows_added) == (2, 1)

    def test_a_sorting_job_covers_the_rows_the_statement_added(self):
        """The job is scoped by statement id, so it covers the new rows and nothing else."""
        from finance_copilot.db.queries.transactions import list_transactions

        conn = _make_conn()
        _ingest(conn, _FakeParser(_days(1, 2, 3)), "a")
        second = _ingest(conn, _FakeParser(_days(1, 2, 3, 4, 5)), "b")
        assert len(list_transactions(conn, statement_id=second.id, unannotated=True)) == 2


class TestOverlapWithNoMatch:
    def test_a_file_over_the_same_days_with_no_payment_in_common_warns(self):
        conn = _make_conn()
        _ingest(conn, _FakeParser(_days(1, 2, 3, 4)), "a")
        other = _ingest(conn, _FakeParser(_chain(50000, [2, 3, 4, 5])), "b")
        assert other.rows_added == 4  # still added: the user may have meant it
        assert other.notices == [
            "This file covers dates already in Kotak ··1111, but none of its payments match. "
            "If it is another account, move it with Move to account."
        ]

    def test_no_warning_when_some_payments_match(self):
        conn = _make_conn()
        _ingest(conn, _FakeParser(_days(1, 2, 3, 4)), "a")
        assert _ingest(conn, _FakeParser(_days(3, 4, 5)), "b").notices == []

    def test_no_warning_when_the_dates_do_not_overlap(self):
        conn = _make_conn()
        _ingest(conn, _FakeParser(_days(1, 2, 3)), "a")
        assert _ingest(conn, _FakeParser(_days(10, 11, 12)), "b").notices == []

    def test_no_warning_for_the_first_statement_of_an_account(self):
        conn = _make_conn()
        assert _ingest(conn, _FakeParser(_days(1, 2, 3)), "a").notices == []

    def test_no_warning_in_a_new_account(self):
        conn = _make_conn()
        _ingest(conn, _FakeParser(_days(1, 2, 3), ref="1111"), "a")
        other = _ingest(conn, _FakeParser(_chain(50000, [1, 2, 3]), ref="2222"), "b")
        assert other.notices == []

    def test_the_warning_comes_back_on_the_upload_response(self, api):
        client, conn = api
        _ingest(conn, _FakeParser(_days(1, 2, 3, 4)), "a")
        parser = _FakeParser(_chain(50000, [2, 3, 4, 5]))
        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=parser):
            r = client.post("/api/statements/upload", files={"file": ("b.pdf", b"%PDF b", "application/pdf")})
        assert r.status_code == 200
        assert any("none of its payments match" in w for w in r.json()["warnings"])


class TestContinuityBesideOverlaps:
    def test_an_overlapping_neighbour_is_not_before_or_after(self):
        from finance_copilot.pipeline.ingest import check_continuity

        conn = _make_conn()
        _ingest(conn, _FakeParser(_days(1, 2, 3, 4)), "a")
        # Overlaps a's last two days.
        b = _ingest(conn, _FakeParser(_days(3, 4, 5, 6)), "b")
        assert check_continuity(conn, b) == []

    def test_a_real_gap_beside_an_overlap_still_warns(self):
        from finance_copilot.pipeline.ingest import check_continuity

        conn = _make_conn()
        _ingest(conn, _FakeParser(_days(1, 2, 3)), "a")
        c = _ingest(conn, _FakeParser(_chain(50000, [10, 11])), "c")
        assert len(check_continuity(conn, c)) == 1

    def _ledger(self, dates):
        """One account's payments as one running chain, a 100 rupee debit on each date."""
        balance, out = 100000.0, []
        for month, day in dates:
            balance -= 100
            out.append(_t(day, balance=balance, month=month))
        return out

    def test_a_file_that_starts_inside_a_neighbour_is_not_missing_the_statement_before_it(self):
        """1 to 10, 11 to 20, then 15 to 25: the third continues the second, whatever
        the first closed at."""
        from finance_copilot.pipeline.ingest import check_continuity

        conn = _make_conn()
        rows = self._ledger([(5, d) for d in range(1, 26)])
        _ingest(conn, _FakeParser(rows[0:10]), "a")
        _ingest(conn, _FakeParser(rows[10:20]), "b")
        c = _ingest(conn, _FakeParser(rows[14:25]), "c")
        assert c.rows_added == 5
        assert check_continuity(conn, c) == []

    def test_a_file_that_ends_inside_a_neighbour_is_not_missing_the_statement_after_it(self):
        from finance_copilot.pipeline.ingest import check_continuity

        conn = _make_conn()
        rows = self._ledger([(5, d) for d in range(1, 26)])
        _ingest(conn, _FakeParser(rows[9:20]), "x")  # 10 to 20
        _ingest(conn, _FakeParser(rows[20:25]), "y")  # 21 to 25
        early = _ingest(conn, _FakeParser(rows[0:15]), "early")  # 1 to 15: ends inside x
        assert early.rows_added == 9
        assert check_continuity(conn, early) == []

    def test_september_october_then_the_middle_of_october_to_the_middle_of_november(self):
        from finance_copilot.pipeline.ingest import check_continuity

        conn = _make_conn()
        dates = (
            [(9, d) for d in (3, 10, 20, 28)]
            + [(10, d) for d in (2, 9, 16, 23, 30)]
            + [(11, d) for d in (1, 8, 15)]
        )
        rows = self._ledger(dates)
        _ingest(conn, _FakeParser(rows[0:4]), "sep")
        _ingest(conn, _FakeParser(rows[4:9]), "oct")
        long = _ingest(conn, _FakeParser(rows[7:]), "mid")  # 23 Oct to 15 Nov: starts inside October
        assert check_continuity(conn, long) == []

    def test_a_real_gap_still_warns_when_the_file_starts_outside_every_neighbour(self):
        from finance_copilot.pipeline.ingest import check_continuity

        conn = _make_conn()
        rows = self._ledger([(5, d) for d in range(1, 26)])
        _ingest(conn, _FakeParser(rows[0:5]), "a")
        gap = _ingest(conn, _FakeParser(rows[10:20]), "gap")
        assert len(check_continuity(conn, gap)) == 1


# --- routes -------------------------------------------------------------------


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The app over a ledger file in a temp folder (an upload queues a sort, which a
    runner reads from the file), with no model: rules and remembered payees only."""
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.config import settings
    from finance_copilot.db import connection as dbc
    from finance_copilot.main import app

    db_path = str(tmp_path / "ledger.db")
    monkeypatch.setattr(settings, "db_path", db_path)
    monkeypatch.setattr(settings, "llm_provider", "none")
    conn = dbc.get_migrated_db(db_path)

    def override():
        c = dbc.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[api_get_db] = override
    with patch("finance_copilot.api.routes.annotations.embed_transaction", return_value=False), \
            patch("finance_copilot.api.routes.annotations._embed_in_background"):
        yield TestClient(app), conn
    app.dependency_overrides.pop(api_get_db, None)
    conn.close()


def _upload(client, name):
    path = FIXTURES / name
    return client.post("/api/statements/upload", files={"file": (name, path.read_bytes(), "application/pdf")})


class TestUploadResponse:
    def test_the_response_says_how_many_are_new_and_how_many_were_in(self, api):
        client, _ = api
        _upload(client, "sample_statement.pdf")
        _upload(client, "sample_statement_nov.pdf")
        body = _upload(client, "sample_statement_overlap.pdf").json()
        assert (body["rows_in_file"], body["rows_added"], body["rows_already_in"]) == (15, 3, 12)
        assert body["transaction_count"] == 3

    def test_a_first_file_has_nothing_already_in(self, api):
        client, _ = api
        body = _upload(client, "sample_statement.pdf").json()
        assert (body["rows_in_file"], body["rows_added"], body["rows_already_in"]) == (8, 8, 0)

    def test_a_file_with_nothing_new_is_refused_in_plain_words(self, api):
        client, conn = api
        _upload(client, "sample_statement_overlap.pdf")
        r = _upload(client, "sample_statement_nov.pdf")
        assert r.status_code == 409
        assert r.json()["detail"] == "Nothing new: all 8 payments in this file are already in Bank ··0000."
        assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 1

    def test_the_balance_check_is_the_files_own(self, api):
        client, _ = api
        _upload(client, "sample_statement.pdf")
        _upload(client, "sample_statement_nov.pdf")
        body = _upload(client, "sample_statement_overlap.pdf").json()
        assert body["balance_check"] == {"checked": 14, "consistent": 14, "balanced": True}

    def test_the_statement_list_carries_the_counts(self, api):
        client, _ = api
        _upload(client, "sample_statement.pdf")
        _upload(client, "sample_statement_nov.pdf")
        _upload(client, "sample_statement_overlap.pdf")
        rows = {s["rows_added"]: s for s in client.get("/api/statements").json()}
        assert rows[3]["rows_in_file"] == 15
        assert rows[3]["txn_count"] == 3
        assert rows[3]["balance_check"]["balanced"] is True

    def test_a_sort_is_queued_for_the_new_rows_only(self, api):
        client, conn = api
        _upload(client, "sample_statement.pdf")
        _upload(client, "sample_statement_nov.pdf")
        body = _upload(client, "sample_statement_overlap.pdf").json()
        job = conn.execute("SELECT statement_id FROM annotation_jobs WHERE id = ?", (body["sorting_job_id"],)).fetchone()
        assert job["statement_id"] == body["id"]
        assert conn.execute("SELECT COUNT(*) FROM transactions WHERE statement_id = ?", (body["id"],)).fetchone()[0] == 3


class TestBalanceCheckIsStoredWithTheFile:
    def test_an_overlap_never_makes_a_statement_look_unbalanced(self, api):
        """The overlapping file's chain is checked as parsed. Its added rows alone are
        not a chain (a run of rows with gaps between them), and must not be read as one."""
        client, conn = api
        _ingest(conn, _FakeParser(_days(1, 3, 5)), "a")
        # The file's chain is 1..6; the rows added are 2, 4 and 6: not consecutive.
        second = _ingest(conn, _FakeParser(_days(1, 2, 3, 4, 5, 6)), "b")
        assert second.rows_added == 3
        checks = {s["id"]: s["balance_check"] for s in client.get("/api/statements").json()}
        assert checks[second.id] == {"checked": 5, "consistent": 5, "balanced": True}

    def test_a_chain_that_does_not_reconcile_is_reported(self):
        from finance_copilot.db.queries.transactions import statement_balance_checks

        conn = _make_conn()
        rows = _chain(10000, [1, 2, 3])
        rows[1] = rows[1].model_copy(update={"running_balance": 1.0})
        st = _ingest(conn, _FakeParser(rows), "bad")
        assert statement_balance_checks(conn)[st.id] == {"checked": 2, "consistent": 0, "balanced": False}

    def test_a_statement_with_no_stored_check_is_computed_from_its_rows(self):
        """A statement written around ingest (the sample data) has no stored check; its rows are read."""
        from finance_copilot.db.queries.transactions import statement_balance_checks

        conn = _make_conn()
        conn.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s', 'kotak', '1', '2026-05')"
        )
        for i, (amount, dc, bal) in enumerate([(100, "debit", 900), (50, "credit", 950), (10, "debit", 5)]):
            conn.execute(
                "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, running_balance) "
                "VALUES (?, 's', '2026-05-01', ?, ?, 'x', ?)", (f"t{i}", amount, dc, bal),
            )
        assert statement_balance_checks(conn)["s"] == {"checked": 2, "consistent": 1, "balanced": False}


class TestMonthsWithPayments:
    def test_a_statement_that_spans_two_months_gives_both(self, api):
        client, _ = api
        _upload(client, "sample_statement_overlap.pdf")
        assert client.get("/api/transactions/months").json() == {"months": ["2026-10", "2026-11"]}

    def test_no_payments_no_months(self, api):
        client, _ = api
        assert client.get("/api/transactions/months").json() == {"months": []}

    def test_it_is_the_same_list_money_map_offers(self, api):
        client, _ = api
        _upload(client, "sample_statement.pdf")
        _upload(client, "sample_statement_overlap.pdf")
        assert client.get("/api/insights").json()["months"] == client.get("/api/transactions/months").json()["months"]


# --- moving a statement into an account that holds its rows ---------------------


def _accounts_pair(conn, rows_a, rows_b):
    """Two statements that landed in different accounts (different numbers)."""
    a = _ingest(conn, _FakeParser(rows_a, ref="1111"), "a")
    b = _ingest(conn, _FakeParser(rows_b, ref="2222"), "b")
    assert a.account_id != b.account_id
    return a, b


class TestMoveIntoAnAccountThatHasItsRows:
    def _annotate(self, conn, txn_id):
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, merchant, category, subcategory, tags, confidence, source) "
            "VALUES (?, ?, 'M', 'Food & Dining', 'Restaurants', '[]', 0.9, 'llm')", (f"ann_{txn_id}", txn_id),
        )

    def test_rows_the_account_already_has_are_dropped_and_the_rest_move(self, api):
        client, conn = api
        a, b = _accounts_pair(conn, _days(1, 2, 3), _days(2, 3, 4, 5))
        r = client.patch(f"/api/statements/{b.id}", json={"account_id": a.account_id})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rows_already_there"] == 2
        assert body["statement_deleted"] is False
        assert body["account_id"] == a.account_id
        assert _count(conn, b.id) == 2
        assert {r["txn_date"] for r in conn.execute("SELECT txn_date FROM transactions WHERE statement_id = ?", (b.id,))} == {
            "2026-05-04", "2026-05-05",
        }
        assert conn.execute("SELECT rows_in_file, rows_added FROM statements WHERE id = ?", (b.id,)).fetchone()[:] == (4, 2)
        # The account that held its first rows keeps them.
        assert _count(conn, a.id) == 3

    def test_the_dropped_rows_take_their_machine_labels_and_embeddings_with_them(self, api):
        client, conn = api
        a, b = _accounts_pair(conn, _days(1, 2), _days(2, 3))
        dup = conn.execute("SELECT id FROM transactions WHERE statement_id = ? AND txn_date = '2026-05-02'", (b.id,)).fetchone()[0]
        kept = conn.execute("SELECT id FROM transactions WHERE statement_id = ? AND txn_date = '2026-05-03'", (b.id,)).fetchone()[0]
        survivor = conn.execute("SELECT id FROM transactions WHERE statement_id = ? AND txn_date = '2026-05-02'", (a.id,)).fetchone()[0]
        self._annotate(conn, dup)
        self._annotate(conn, kept)
        conn.execute("INSERT INTO embedding_meta (id, transaction_id, model_version) VALUES ('e1', ?, 'm')", (dup,))
        conn.commit()
        body = client.patch(f"/api/statements/{b.id}", json={"account_id": a.account_id}).json()
        assert conn.execute("SELECT COUNT(*) FROM transactions WHERE id = ?", (dup,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM annotations WHERE transaction_id IN (?, ?)", (dup, survivor)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM embedding_meta WHERE transaction_id = ?", (dup,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM annotations WHERE transaction_id = ?", (kept,)).fetchone()[0] == 1
        # A machine label is not the user's work: nothing was carried over.
        assert body["rows_already_there"] == 1
        assert body["work_carried_over"] == 0

    def test_a_statement_with_nothing_left_is_deleted_with_its_emptied_account(self, api):
        client, conn = api
        a, b = _accounts_pair(conn, _days(1, 2, 3), _days(2, 3))
        r = client.patch(f"/api/statements/{b.id}", json={"account_id": a.account_id})
        assert r.status_code == 200
        body = r.json()
        assert (body["rows_already_there"], body["statement_deleted"]) == (2, True)
        assert conn.execute("SELECT COUNT(*) FROM statements WHERE id = ?", (b.id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1

    def test_a_queued_sort_of_a_deleted_statement_goes_with_it(self, api):
        client, conn = api
        a, b = _accounts_pair(conn, _days(1, 2, 3), _days(2, 3))
        conn.execute("INSERT INTO annotation_jobs (id, status, statement_id, kind) VALUES ('j1', 'queued', ?, 'annotate')", (b.id,))
        conn.commit()
        client.patch(f"/api/statements/{b.id}", json={"account_id": a.account_id})
        assert conn.execute("SELECT COUNT(*) FROM annotation_jobs WHERE statement_id = ?", (b.id,)).fetchone()[0] == 0

    def test_a_move_where_nothing_is_there_already_says_so(self, api):
        client, conn = api
        a, b = _accounts_pair(conn, _days(1, 2), _chain(50000, [1, 2]))
        body = client.patch(f"/api/statements/{b.id}", json={"account_id": a.account_id}).json()
        assert (body["rows_already_there"], body["statement_deleted"]) == (0, False)
        assert _count(conn, b.id) == 2

    def test_moving_to_a_new_account_drops_nothing(self, api):
        client, conn = api
        a = _ingest(conn, _FakeParser(_days(1, 2, 3)), "a")
        b = _ingest(conn, _FakeParser(_days(5, 6, 7)), "b")
        body = client.patch(f"/api/statements/{b.id}", json={"new_account": True, "name": "Joint"}).json()
        assert (body["rows_already_there"], body["statement_deleted"]) == (0, False)
        assert _count(conn, b.id) == 3
        assert a.account_id != body["account_id"]

    def test_moving_where_it_already_is_changes_nothing(self, api):
        client, conn = api
        a = _ingest(conn, _FakeParser(_days(1, 2, 3)), "a")
        body = client.patch(f"/api/statements/{a.id}", json={"account_id": a.account_id}).json()
        assert (body["rows_already_there"], body["statement_deleted"]) == (0, False)
        assert _count(conn, a.id) == 3

    def test_a_statement_that_would_lose_rows_is_not_moved_while_it_is_sorted(self, api):
        client, conn = api
        a, b = _accounts_pair(conn, _days(1, 2, 3), _days(2, 3, 4))
        conn.execute("INSERT INTO annotation_jobs (id, status, statement_id, kind) VALUES ('j1', 'running', ?, 'annotate')", (b.id,))
        conn.commit()
        assert client.patch(f"/api/statements/{b.id}", json={"account_id": a.account_id}).status_code == 409
        assert _count(conn, b.id) == 3


class TestMoveCarriesTheUsersWorkOver:
    """A duplicate is dropped, but what the user did to it is theirs: it goes onto
    the payment the account already had, in the same step."""

    def _setup(self, conn):
        a, b = _accounts_pair(conn, _days(1, 2, 3), _days(2, 3, 4, 5))
        dup = conn.execute("SELECT id FROM transactions WHERE statement_id = ? AND txn_date = '2026-05-02'", (b.id,)).fetchone()[0]
        other = conn.execute("SELECT id FROM transactions WHERE statement_id = ? AND txn_date = '2026-05-05'", (b.id,)).fetchone()[0]
        survivor = conn.execute("SELECT id FROM transactions WHERE statement_id = ? AND txn_date = '2026-05-02'", (a.id,)).fetchone()[0]
        return a, b, dup, other, survivor

    def _label(self, conn, txn_id, source, category="Health", merchant="Dentist", original_source=None):
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, merchant, category, confidence, source, original_source) "
            "VALUES (?, ?, ?, ?, 1.0, ?, ?)", (f"ann_{txn_id}", txn_id, merchant, category, source, original_source),
        )

    def _move(self, client, a, b):
        r = client.patch(f"/api/statements/{b.id}", json={"account_id": a.account_id})
        assert r.status_code == 200, r.text
        return r.json()

    def _annotation(self, conn, txn_id):
        row = conn.execute("SELECT category, source, original_source FROM annotations WHERE transaction_id = ?", (txn_id,)).fetchone()
        return None if row is None else tuple(row)

    def test_a_manual_label_replaces_a_machine_label_and_keeps_its_source(self, api):
        client, conn = api
        a, b, dup, _, survivor = self._setup(conn)
        self._label(conn, survivor, "llm", category="Shopping", merchant="Shop")
        self._label(conn, dup, "manual", original_source="rag_direct")
        conn.commit()
        body = self._move(client, a, b)
        assert self._annotation(conn, survivor) == ("Health", "manual", "rag_direct")
        assert body["work_carried_over"] == 1

    def test_an_imported_or_user_rule_label_is_carried_too(self, api):
        client, conn = api
        a, b = _accounts_pair(conn, _days(1, 2, 3), _days(2, 3, 4))
        for day, source in ((2, "imported"), (3, "user_rule")):
            t = conn.execute("SELECT id FROM transactions WHERE statement_id = ? AND txn_date = ?", (b.id, f"2026-05-0{day}")).fetchone()[0]
            self._label(conn, t, source)
        conn.commit()
        body = self._move(client, a, b)
        sources = {
            r["txn_date"]: r["source"]
            for r in conn.execute("SELECT t.txn_date, a.source FROM annotations a JOIN transactions t ON t.id = a.transaction_id")
        }
        assert sources == {"2026-05-02": "imported", "2026-05-03": "user_rule"}
        assert body["work_carried_over"] == 2

    def test_the_survivors_own_manual_label_wins(self, api):
        client, conn = api
        a, b, dup, _, survivor = self._setup(conn)
        self._label(conn, survivor, "manual", category="Food & Dining", merchant="Cafe")
        self._label(conn, dup, "manual", category="Health")
        conn.commit()
        body = self._move(client, a, b)
        assert self._annotation(conn, survivor) == ("Food & Dining", "manual", None)
        assert conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 1
        assert body["work_carried_over"] == 0

    def test_a_manual_label_goes_onto_a_survivor_that_has_none(self, api):
        client, conn = api
        a, b, dup, _, survivor = self._setup(conn)
        self._label(conn, dup, "manual")
        conn.commit()
        self._move(client, a, b)
        assert self._annotation(conn, survivor) == ("Health", "manual", None)

    def test_a_machine_label_on_the_duplicate_does_not_replace_anything(self, api):
        client, conn = api
        a, b, dup, _, survivor = self._setup(conn)
        self._label(conn, survivor, "llm", category="Shopping")
        conn.execute("DELETE FROM annotations WHERE transaction_id = ?", (dup,))
        self._label(conn, dup, "rag_direct", category="Health")
        conn.commit()
        self._move(client, a, b)
        assert self._annotation(conn, survivor) == ("Shopping", "llm", None)

    def test_a_note_and_a_name_go_onto_a_survivor_that_has_none_and_the_survivors_own_win(self, api):
        client, conn = api
        a, b = _accounts_pair(conn, _days(1, 2, 3), _days(2, 3, 4))
        rows = {
            r["txn_date"]: r["id"]
            for r in conn.execute("SELECT id, txn_date FROM transactions WHERE statement_id = ?", (b.id,))
        }
        survivors = {
            r["txn_date"]: r["id"]
            for r in conn.execute("SELECT id, txn_date FROM transactions WHERE statement_id = ?", (a.id,))
        }
        conn.execute("UPDATE transactions SET user_note = 'Root canal', display_name = 'Dr Rao' WHERE id = ?", (rows["2026-05-02"],))
        conn.execute("UPDATE transactions SET user_note = 'from the move', display_name = 'moved' WHERE id = ?", (rows["2026-05-03"],))
        conn.execute("UPDATE transactions SET user_note = 'mine', display_name = 'Mine' WHERE id = ?", (survivors["2026-05-03"],))
        conn.commit()
        body = self._move(client, a, b)
        got = {
            r["id"]: (r["user_note"], r["display_name"])
            for r in conn.execute("SELECT id, user_note, display_name FROM transactions")
        }
        assert got[survivors["2026-05-02"]] == ("Root canal", "Dr Rao")
        assert got[survivors["2026-05-03"]] == ("mine", "Mine")
        assert body["work_carried_over"] == 1

    def test_group_memberships_follow_and_a_survivor_already_in_the_group_is_not_doubled(self, api):
        client, conn = api
        a, b = _accounts_pair(conn, _days(1, 2, 3), _days(2, 3, 4))
        rows = {
            r["txn_date"]: r["id"]
            for r in conn.execute("SELECT id, txn_date FROM transactions WHERE statement_id = ?", (b.id,))
        }
        survivors = {
            r["txn_date"]: r["id"]
            for r in conn.execute("SELECT id, txn_date FROM transactions WHERE statement_id = ?", (a.id,))
        }
        conn.execute("INSERT INTO transaction_groups (id, name) VALUES ('g1', 'Trip')")
        conn.execute("INSERT INTO transaction_group_members (group_id, transaction_id, role) VALUES ('g1', ?, 'paid')", (rows["2026-05-02"],))
        conn.execute("INSERT INTO transaction_group_members (group_id, transaction_id, role) VALUES ('g1', ?, 'paid')", (rows["2026-05-03"],))
        conn.execute("INSERT INTO transaction_group_members (group_id, transaction_id, role) VALUES ('g1', ?, 'paid')", (survivors["2026-05-03"],))
        conn.commit()
        body = self._move(client, a, b)
        members = sorted(r[0] for r in conn.execute("SELECT transaction_id FROM transaction_group_members WHERE group_id = 'g1'"))
        assert members == sorted([survivors["2026-05-02"], survivors["2026-05-03"]])
        assert body["work_carried_over"] == 1

    def test_a_link_follows_to_the_survivor_in_order(self, api):
        client, conn = api
        a, b, dup, other, survivor = self._setup(conn)
        lo, hi = sorted([dup, other])
        conn.execute("INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES ('l1', ?, ?, 'reimbursement')", (lo, hi))
        conn.commit()
        body = self._move(client, a, b)
        link = conn.execute("SELECT txn_a, txn_b FROM transaction_links").fetchall()
        assert [tuple(r) for r in link] == [tuple(sorted([survivor, other]))]
        assert body["work_carried_over"] == 1

    def test_a_link_that_would_join_a_payment_to_itself_or_repeat_one_is_dropped(self, api):
        client, conn = api
        a, b = _accounts_pair(conn, _days(1, 2, 3), _days(2, 3, 4))
        rows = {
            r["txn_date"]: r["id"]
            for r in conn.execute("SELECT id, txn_date FROM transactions WHERE statement_id = ?", (b.id,))
        }
        survivors = {
            r["txn_date"]: r["id"]
            for r in conn.execute("SELECT id, txn_date FROM transactions WHERE statement_id = ?", (a.id,))
        }

        def link(link_id, x, y, kind="transfer"):
            lo, hi = sorted([x, y])
            conn.execute("INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES (?, ?, ?, ?)", (link_id, lo, hi, kind))

        # The two copies of the 2nd were linked to each other: after the move that is a payment to itself.
        link("self", rows["2026-05-02"], survivors["2026-05-02"])
        # The duplicate of the 3rd and the survivor of the 2nd are linked, and so are both 3rds with it: a repeat.
        link("a", rows["2026-05-03"], survivors["2026-05-02"])
        link("b", survivors["2026-05-03"], survivors["2026-05-02"])
        # A link of another kind between the same two payments is not a repeat.
        link("c", rows["2026-05-03"], survivors["2026-05-02"], kind="refund")
        conn.commit()
        self._move(client, a, b)
        left = sorted((r["id"], r["link_type"]) for r in conn.execute("SELECT id, link_type FROM transaction_links"))
        assert left == [("b", "transfer"), ("c", "refund")]
        for r in conn.execute("SELECT txn_a, txn_b FROM transaction_links"):
            assert r["txn_a"] < r["txn_b"]

    def test_the_reviewers_setup_loses_nothing(self, api):
        """A manual label, a note, a name, a group and a reimbursement link on a
        duplicate (the probe from the review of this part)."""
        client, conn = api
        a, b, dup, other, survivor = self._setup(conn)
        self._label(conn, survivor, "llm", category="Shopping", merchant="Shop")
        self._label(conn, dup, "manual")
        conn.execute("UPDATE transactions SET user_note = 'Root canal, claim from insurer', display_name = 'Dr Rao' WHERE id = ?", (dup,))
        conn.execute("INSERT INTO transaction_groups (id, name) VALUES ('g1', 'Insurance claim')")
        conn.execute("INSERT INTO transaction_group_members (group_id, transaction_id, role) VALUES ('g1', ?, 'paid')", (dup,))
        lo, hi = sorted([dup, other])
        conn.execute("INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES ('l1', ?, ?, 'reimbursement')", (lo, hi))
        conn.commit()
        body = self._move(client, a, b)
        assert conn.execute("SELECT COUNT(*) FROM transactions WHERE id = ?", (dup,)).fetchone()[0] == 0
        assert self._annotation(conn, survivor) == ("Health", "manual", None)
        assert tuple(conn.execute("SELECT user_note, display_name FROM transactions WHERE id = ?", (survivor,)).fetchone()) == (
            "Root canal, claim from insurer", "Dr Rao",
        )
        assert [r[0] for r in conn.execute("SELECT transaction_id FROM transaction_group_members WHERE group_id = 'g1'")] == [survivor]
        assert [tuple(r) for r in conn.execute("SELECT txn_a, txn_b FROM transaction_links")] == [tuple(sorted([survivor, other]))]
        assert (body["rows_already_there"], body["work_carried_over"]) == (2, 1)

    def test_a_failed_move_carries_nothing_over(self, api):
        """The carry and the delete are one step: a statement being sorted is refused before either."""
        client, conn = api
        a, b, dup, _, survivor = self._setup(conn)
        conn.execute("UPDATE transactions SET user_note = 'keep me' WHERE id = ?", (dup,))
        conn.execute("INSERT INTO annotation_jobs (id, status, statement_id, kind) VALUES ('j1', 'running', ?, 'annotate')", (b.id,))
        conn.commit()
        assert client.patch(f"/api/statements/{b.id}", json={"account_id": a.account_id}).status_code == 409
        assert conn.execute("SELECT user_note FROM transactions WHERE id = ?", (survivor,)).fetchone()[0] is None
        assert conn.execute("SELECT user_note FROM transactions WHERE id = ?", (dup,)).fetchone()[0] == "keep me"

    def test_a_statement_with_nothing_left_still_carries_the_work_to_the_account(self, api):
        client, conn = api
        a, b = _accounts_pair(conn, _days(1, 2, 3), _days(2, 3))
        dup = conn.execute("SELECT id FROM transactions WHERE statement_id = ? AND txn_date = '2026-05-03'", (b.id,)).fetchone()[0]
        survivor = conn.execute("SELECT id FROM transactions WHERE statement_id = ? AND txn_date = '2026-05-03'", (a.id,)).fetchone()[0]
        conn.execute("UPDATE transactions SET user_note = 'kept' WHERE id = ?", (dup,))
        conn.commit()
        body = self._move(client, a, b)
        assert body["statement_deleted"] is True
        assert conn.execute("SELECT user_note FROM transactions WHERE id = ?", (survivor,)).fetchone()[0] == "kept"
        assert body["work_carried_over"] == 1



# --- what deleting a statement takes with it ----------------------------------


class TestSharedRows:
    def test_the_list_counts_rows_a_later_file_also_had(self, api):
        client, _ = api
        _upload(client, "sample_statement.pdf")
        _upload(client, "sample_statement_nov.pdf")
        _upload(client, "sample_statement_overlap.pdf")
        by_added = {s["rows_added"]: s for s in client.get("/api/statements").json()}
        # October's and November's rows 5th-8th Oct and all of Nov were in the later file too.
        october = next(s for s in client.get("/api/statements").json() if s["period_start"] == "2026-10-01")
        november = next(s for s in client.get("/api/statements").json() if s["period_start"] == "2026-11-01")
        assert october["shared_with_others"] == 4
        assert november["shared_with_others"] == 8
        # The later file's own rows are in no file that came after it.
        assert by_added[3]["shared_with_others"] == 0

    def test_a_statement_nothing_overlaps_has_none(self, api):
        client, _ = api
        _upload(client, "sample_statement.pdf")
        _upload(client, "sample_statement_nov.pdf")
        assert [s["shared_with_others"] for s in client.get("/api/statements").json()] == [0, 0]

    def test_a_statement_moved_in_after_the_fact_counts_for_the_one_that_was_there(self, api):
        """The first file landed in the wrong account, the right account's file came
        second; moving the first in drops the days both had. Deleting the second
        takes them for good, though it was added after the first."""
        client, conn = api
        wrong = _ingest(conn, _FakeParser(_days(1, 2, 3, 4, 5), ref="2222"), "wrong")
        right = _ingest(conn, _FakeParser(_days(4, 5, 6, 7, 8), ref="1111"), "right")
        assert client.patch(f"/api/statements/{wrong.id}", json={"account_id": right.account_id}).status_code == 200
        by_id = {s["id"]: s for s in client.get("/api/statements").json()}
        # The moved statement skipped the 4th and 5th, which the second file holds.
        assert (by_id[wrong.id]["rows_in_file"], by_id[wrong.id]["rows_added"]) == (5, 3)
        assert by_id[right.id]["shared_with_others"] == 2
        assert by_id[wrong.id]["shared_with_others"] == 0

    def test_an_earlier_file_that_skipped_nothing_does_not_count(self, api):
        """Only a file that skipped payments left another statement as their holder."""
        client, _ = api
        _upload(client, "sample_statement_overlap.pdf")
        _upload(client, "sample_statement.pdf")
        by_start = {s["period_start"]: s for s in client.get("/api/statements").json()}
        assert by_start["2026-10-05"]["shared_with_others"] == 4  # the 5th to the 8th, which October also has
        assert by_start["2026-10-01"]["shared_with_others"] == 0

    def test_deleting_the_first_does_not_bring_back_what_the_later_file_skipped(self, api):
        client, conn = api
        october = _upload(client, "sample_statement.pdf").json()
        _upload(client, "sample_statement_overlap.pdf")
        assert client.delete(f"/api/statements/{october['id']}").status_code == 200
        # October's rows are gone; the overlap file added only the November rows and what followed.
        assert conn.execute("SELECT COUNT(*) FROM transactions WHERE txn_date < '2026-10-09'").fetchone()[0] == 0
