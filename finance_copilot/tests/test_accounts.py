"""Accounts: resolution at ingest, the label, continuity per account, the routes."""
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
from finance_copilot.db.queries.accounts import (
    account_label,
    resolve_account,
)
from finance_copilot.models.transaction import Transaction
from finance_copilot.parsers.base import StatementParser
from finance_copilot.pipeline.ingest import check_continuity, ingest_pdf

FIXTURES = Path(__file__).parent / "fixtures"


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


# --- the label ------------------------------------------------------------


class TestLabel:
    @pytest.mark.parametrize("bank,ref,expected", [
        ("kotak", "3250508074", "Kotak ··8074"),
        ("kotak", None, "Kotak"),
        ("kotak", "", "Kotak"),
        ("hdfc", "XXXX0000", "HDFC ··0000"),
        ("hdfc (auto)", "5010 1234 5678", "HDFC ··5678"),
        ("sbi", "123", "SBI ··123"),
        ("icici", "XXXX-1234", "ICICI ··1234"),
        ("axis", None, "Axis"),
        ("idfc", None, "IDFC FIRST"),
        ("generic", "XXXX0000", "Bank ··0000"),
        ("generic", None, "Bank"),
        ("some small bank", None, "Some Small Bank"),
        ("", None, "Bank"),
        ("   ", "XXXX0000", "Bank ··0000"),
        (" (auto)", None, "Bank"),
        ("Kotak", "3250508074", "Kotak ··8074"),
        ("kotak ", None, "Kotak"),
        ("Foo (AUTO)", None, "Foo"),
        ("state bank (auto)", "1234", "SBI ··1234"),
        ("State Bank (AUTO)", None, "SBI"),
    ])
    def test_derived_from_the_bank_and_the_last_four_digits(self, bank, ref, expected):
        assert account_label(None, bank, ref) == expected

    def test_the_users_name_wins(self):
        assert account_label("Kotak savings", "kotak", "3250508074") == "Kotak savings"

    def test_a_blank_name_is_no_name(self):
        assert account_label("  ", "kotak", "3250508074") == "Kotak ··8074"

    def test_the_label_never_holds_the_full_number(self):
        assert "3250508074" not in account_label(None, "kotak", "3250508074")

    def test_generic_never_shows(self):
        assert "generic" not in account_label(None, "generic", "XXXX0000").lower()


# --- resolution -----------------------------------------------------------


def _account_count(conn):
    return conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]


def _row(conn, account_id):
    return conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()


def _give(conn, account_id, stmt_id="s", month="2026-01"):
    """A statement in the account, so it is not empty."""
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month, account_id) "
        "VALUES (?, 'x', '1', ?, ?)", (stmt_id, month, account_id))
    conn.commit()


class TestResolveAccount:
    def test_a_first_statement_creates_the_account(self):
        conn = _make_conn()
        account_id, created = resolve_account(conn, "kotak", "3250508074")
        assert created is True
        row = _row(conn, account_id)
        assert (row["bank_name"], row["account_ref"], row["name"]) == ("kotak", "3250508074", None)

    def test_same_bank_and_number_is_the_same_account(self):
        conn = _make_conn()
        first, _ = resolve_account(conn, "kotak", "3250508074")
        second, created = resolve_account(conn, "kotak", "3250508074")
        assert (second, created) == (first, False)
        assert _account_count(conn) == 1

    def test_the_automatic_marker_is_the_same_bank(self):
        conn = _make_conn()
        first, _ = resolve_account(conn, "hdfc (auto)", "5010")
        second, created = resolve_account(conn, "hdfc", "5010")
        assert (second, created) == (first, False)
        assert _row(conn, first)["bank_name"] == "hdfc"

    def test_a_state_bank_read_by_the_generic_parser_is_sbi(self):
        """0.1.2 wrote "state bank (auto)" for an SBI statement: the same bank as "sbi"."""
        conn = _make_conn()
        first, _ = resolve_account(conn, "state bank (auto)", "5010")
        second, created = resolve_account(conn, "sbi", "5010")
        assert (second, created) == (first, False)
        assert _row(conn, first)["bank_name"] == "sbi"

    def test_another_number_at_the_same_bank_is_another_account(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        b, created = resolve_account(conn, "kotak", "2222")
        assert created is True and a != b

    def test_another_bank_is_another_account_even_with_the_same_number(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        b, created = resolve_account(conn, "hdfc", "1111")
        assert created is True and a != b

    def test_a_number_adopts_the_banks_only_unnumbered_account(self):
        """Older statements had no header: the first one with a number is the same account."""
        conn = _make_conn()
        old, _ = resolve_account(conn, "kotak", None)
        _give(conn, old)
        new, created = resolve_account(conn, "kotak", "3250508074")
        assert (new, created) == (old, False)
        assert _row(conn, old)["account_ref"] == "3250508074"
        assert _account_count(conn) == 1

    def test_a_number_does_not_adopt_when_the_bank_has_other_accounts(self):
        """With A and an unknown already there, a new number B is not the unknown's: no guessing."""
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        unknown = "acc_unknown"  # what a migrated ledger holds
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES (?, 'kotak', NULL)", (unknown,))
        b, created = resolve_account(conn, "kotak", "2222")
        assert created is True and b not in (a, unknown)
        assert _row(conn, unknown)["account_ref"] is None

    def test_no_number_joins_the_banks_only_account(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        again, created = resolve_account(conn, "kotak", None)
        assert (again, created) == (a, False)

    def test_no_number_with_no_account_at_the_bank_starts_an_unnumbered_one(self):
        conn = _make_conn()
        resolve_account(conn, "hdfc", "1")
        account_id, created = resolve_account(conn, "kotak", None)
        assert created is True
        assert _row(conn, account_id)["account_ref"] is None

    def test_no_number_with_several_accounts_starts_one_unknown_account_then_reuses_it(self):
        conn = _make_conn()
        resolve_account(conn, "kotak", "1111")
        resolve_account(conn, "kotak", "2222")
        unknown, created = resolve_account(conn, "kotak", None)
        assert created is True
        again, created_again = resolve_account(conn, "kotak", "")
        assert (again, created_again) == (unknown, False)
        assert _account_count(conn) == 3

    def test_bank_keys_compare_trimmed_and_ignoring_case(self):
        conn = _make_conn()
        first, _ = resolve_account(conn, "Kotak", "3250508074")
        for spelling in ("kotak", " KOTAK ", "kotak (AUTO)", "Kotak (auto) "):
            again, created = resolve_account(conn, spelling, "3250508074")
            assert (again, created) == (first, False), spelling
        assert _row(conn, first)["bank_name"] == "kotak"

    def test_a_numbered_unknown_bank_never_adopts_the_only_account(self):
        """"generic" is not one bank: a numbered statement of it joins only on the same number."""
        conn = _make_conn()
        old, _ = resolve_account(conn, "generic", None)
        _give(conn, old)
        new, created = resolve_account(conn, "generic", "XXXX1234")
        assert created is True and new != old
        assert _row(conn, old)["account_ref"] is None
        again, created_again = resolve_account(conn, "generic", "XXXX1234")
        assert (again, created_again) == (new, False)

    def test_a_known_bank_still_adopts_the_only_unnumbered_account(self):
        conn = _make_conn()
        old, _ = resolve_account(conn, "hdfc", None)
        _give(conn, old)
        assert resolve_account(conn, "hdfc", "XXXX1234") == (old, False)

    def test_an_account_gets_a_ulid_style_id(self):
        conn = _make_conn()
        account_id, _ = resolve_account(conn, "kotak", "1")
        assert account_id.startswith("acc_") and len(account_id) > 10


def _period_statement(conn, stmt_id, bank, ref, account_id, month, uploaded="2026-01-01 10:00:00"):
    """A statement whose period is its month, uploaded when it says."""
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month, account_ref, account_id, "
        "period_start, period_end, uploaded_at) VALUES (?, ?, '1', ?, ?, ?, ?, ?, ?)",
        (stmt_id, bank, month, ref, account_id, f"{month}-01", f"{month}-28", uploaded),
    )
    conn.commit()


class TestTheLatestMonthDecides:
    """The next statement continues the account that holds the latest month, not the one
    uploaded last: re-uploading an old month must not drag the chain after it."""

    def test_an_old_month_uploaded_late_does_not_decide(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        joint = "acc_joint"
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES (?, 'kotak', '1111')", (joint,))
        _period_statement(conn, "mar", "kotak", "1111", joint, "2026-03", "2026-03-31 10:00:00")
        _period_statement(conn, "jan", "kotak", "1111", a, "2026-01", "2026-06-01 10:00:00")
        assert resolve_account(conn, "kotak", "1111") == (joint, False)

    def test_the_same_month_in_two_accounts_goes_to_the_one_uploaded_last(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        b = "acc_b"
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES (?, 'kotak', '1111')", (b,))
        _period_statement(conn, "x", "kotak", "1111", a, "2026-03", "2026-03-31 10:00:00")
        _period_statement(conn, "y", "kotak", "1111", b, "2026-03", "2026-04-02 10:00:00")
        assert resolve_account(conn, "kotak", "1111") == (b, False)

    def test_a_statement_with_no_payments_has_no_period_and_never_beats_one_that_has(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        b = "acc_b"
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES (?, 'kotak', '1111')", (b,))
        _period_statement(conn, "dated", "kotak", "1111", a, "2026-02", "2026-02-28 10:00:00")
        _statement(conn, "empty", "kotak", "1111", b, "2026-05")
        assert resolve_account(conn, "kotak", "1111") == (a, False)

    def test_split_the_latest_month_fix_an_old_one_and_the_next_still_follows_the_latest(self, api):
        """The reviewer's sequence: Jan, Feb, Mar; Mar split to Joint; Apr goes to Joint;
        Jan deleted, uploaded again and moved back; May still goes to Joint."""
        client, conn = api
        jan = _ingest(conn, _FakeParser("kotak", "1111", _rows(10000, 1, month=1)), "jan")
        feb = _ingest(conn, _FakeParser("kotak", "1111", _rows(9700, 1, month=2)), "feb")
        mar = _ingest(conn, _FakeParser("kotak", "1111", _rows(9400, 1, month=3)), "mar")
        assert client.patch(f"/api/statements/{mar.id}", json={"new_account": True, "name": "Joint"}).status_code == 200
        joint = conn.execute("SELECT account_id FROM statements WHERE id = ?", (mar.id,)).fetchone()[0]
        apr = _ingest(conn, _FakeParser("kotak", "1111", _rows(9100, 1, month=4)), "apr")
        assert apr.account_id == joint
        assert client.delete(f"/api/statements/{jan.id}").status_code == 200
        jan2 = _ingest(conn, _FakeParser("kotak", "1111", _rows(10000, 1, month=1)), "jan2")
        assert client.patch(f"/api/statements/{jan2.id}", json={"account_id": feb.account_id}).status_code == 200
        may = _ingest(conn, _FakeParser("kotak", "1111", _rows(8800, 1, month=5)), "may")
        assert may.account_id == joint and may.account_is_new is False


class TestAStatementWithNoNumber:
    """A statement that printed no number follows an earlier unnumbered one only into an
    account that has no number either."""

    def test_it_does_not_follow_an_unnumbered_statement_into_a_numbered_account_of_a_bank_with_several(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", None)
        _period_statement(conn, "u1", "kotak", None, a, "2026-01")
        assert resolve_account(conn, "kotak", "1111") == (a, False)  # adopted: the number is a
        _period_statement(conn, "n1", "kotak", "1111", a, "2026-02")
        b, created = resolve_account(conn, "kotak", "2222")
        assert created is True
        _period_statement(conn, "n2", "kotak", "2222", b, "2026-03")
        # Two accounts, neither unnumbered: the unnumbered statement gets one of its own.
        u2, created = resolve_account(conn, "kotak", None)
        assert created is True and u2 not in (a, b)
        assert _row(conn, u2)["account_ref"] is None
        # And the one after it follows that one.
        _period_statement(conn, "u2", "kotak", None, u2, "2026-04")
        assert resolve_account(conn, "kotak", None) == (u2, False)

    def test_it_still_follows_the_banks_only_account(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        _period_statement(conn, "n1", "kotak", "1111", a, "2026-01")
        _period_statement(conn, "u1", "kotak", None, a, "2026-02")
        assert resolve_account(conn, "kotak", None) == (a, False)

    def test_it_joins_the_banks_single_unnumbered_account(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        b, _ = resolve_account(conn, "kotak", "2222")
        unnumbered = "acc_unnumbered"
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES (?, 'kotak', NULL)", (unnumbered,))
        assert resolve_account(conn, "kotak", None) == (unnumbered, False)
        assert unnumbered not in (a, b)

    def test_a_moved_unnumbered_statement_is_followed_into_an_account_without_a_number(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        resolve_account(conn, "kotak", "2222")
        elsewhere = "acc_elsewhere"
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES (?, 'kotak', NULL)", (elsewhere,))
        _period_statement(conn, "u1", "kotak", None, elsewhere, "2026-01")
        # Another unnumbered account appears; the user's last move still decides.
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES ('acc_other', 'kotak', NULL)")
        assert resolve_account(conn, "kotak", None) == (elsewhere, False)


def _statement(conn, stmt_id, bank, ref, account_id, month="2026-01"):
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month, account_ref, account_id) "
        "VALUES (?, ?, '1', ?, ?, ?)", (stmt_id, bank, month, ref, account_id))
    conn.commit()


class TestAMoveIsRemembered:
    """The next statement of the same bank and number goes where the user put the last one."""

    @pytest.mark.parametrize("ref", ["3250508074", None])
    def test_the_next_statement_joins_the_account_the_last_one_was_moved_to(self, ref):
        conn = _make_conn()
        landed, _ = resolve_account(conn, "kotak", ref)
        _statement(conn, "s1", "kotak", ref, landed, "2026-01")
        other = "acc_other"  # an account to move into; a statement with no number only follows to an account with none
        other_ref = "9999999999" if ref else None
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES (?, 'kotak', ?)", (other, other_ref))
        _statement(conn, "s2", "kotak", other_ref, other, "2026-01")
        conn.execute("UPDATE statements SET account_id = ? WHERE id = 's1'", (other,))
        conn.commit()
        nxt, created = resolve_account(conn, "kotak", ref)
        assert (nxt, created) == (other, False)

    def test_it_is_the_most_recent_statement_that_decides(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        b = "acc_b"
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES (?, 'kotak', '1111')", (b,))
        conn.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month, account_ref, account_id, uploaded_at) "
            "VALUES ('old', 'kotak', '1', '2026-01', '1111', ?, '2026-01-01 10:00:00')", (a,))
        conn.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month, account_ref, account_id, uploaded_at) "
            "VALUES ('new', 'kotak (auto)', '1', '2026-02', '1111', ?, '2026-02-01 10:00:00')", (b,))
        conn.commit()
        assert resolve_account(conn, "kotak", "1111") == (b, False)

    def test_a_split_leaves_two_accounts_with_the_same_bank_and_number(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        _statement(conn, "s1", "kotak", "1111", a)
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES ('acc_split', 'kotak', '1111')")
        conn.execute("UPDATE statements SET account_id = 'acc_split' WHERE id = 's1'")
        conn.commit()
        assert resolve_account(conn, "kotak", "1111") == ("acc_split", False)

    def test_another_bank_or_number_is_not_remembered_across(self):
        conn = _make_conn()
        a, _ = resolve_account(conn, "kotak", "1111")
        _statement(conn, "s1", "kotak", "1111", a)
        assert resolve_account(conn, "hdfc", "1111")[1] is True
        assert resolve_account(conn, "kotak", "2222")[1] is True


# --- ingest ----------------------------------------------------------------


class _FakeParser(StatementParser):
    version = "1"

    def __init__(self, bank, ref, rows, period=None):
        self.bank_name = bank
        self._ref = ref
        self._rows = rows
        self._period = period

    def detect(self, path, password=None):
        return True

    def parse(self, path, password=None):
        # Fresh ids per parse, like a parser reading a new file.
        return [t.model_copy(update={"id": str(ulid.ULID())}) for t in self._rows]

    def extract_account_ref(self, path, password=None):
        return self._ref

    def extract_period(self, path, password=None):
        return self._period


def _rows(start_balance, day_from, days=3, month=5):
    out, balance = [], start_balance
    for i in range(days):
        balance -= 100
        out.append(Transaction(
            txn_date=date(2026, month, day_from + i), amount=100.0, debit_credit="debit",
            raw_description=f"UPI/X/{day_from + i}/UPI", running_balance=balance,
        ))
    return out


def _ingest(conn, parser, tag):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"%PDF " + tag.encode())
        path = tmp.name
    try:
        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=parser):
            return ingest_pdf(path, conn=conn)
    finally:
        Path(path).unlink()


class TestIngestAssignsTheAccount:
    def test_every_statement_has_an_account_after_ingest(self):
        conn = _make_conn()
        s = _ingest(conn, _FakeParser("kotak", "1111", _rows(10000, 1)), "a")
        assert s.account_id is not None and s.account_is_new is True
        stored = conn.execute("SELECT account_id FROM statements WHERE id = ?", (s.id,)).fetchone()
        assert stored["account_id"] == s.account_id

    def test_a_second_statement_of_the_account_joins_it(self):
        conn = _make_conn()
        a = _ingest(conn, _FakeParser("kotak", "1111", _rows(10000, 1)), "a")
        b = _ingest(conn, _FakeParser("kotak", "1111", _rows(9700, 4)), "b")
        assert b.account_id == a.account_id and b.account_is_new is False
        assert _account_count(conn) == 1

    def test_the_bank_name_the_parser_reports_is_kept_on_the_statement(self):
        conn = _make_conn()
        s = _ingest(conn, _FakeParser("hdfc (auto)", "1111", _rows(10000, 1)), "a")
        assert conn.execute("SELECT bank_name FROM statements WHERE id = ?", (s.id,)).fetchone()[0] == "hdfc (auto)"
        assert _row(conn, s.account_id)["bank_name"] == "hdfc"

    def test_no_statement_is_ever_left_without_an_account(self):
        conn = _make_conn()
        for i, (bank, ref) in enumerate([("kotak", None), ("kotak", "1"), ("kotak", "2"), ("kotak", None), ("hdfc", None)]):
            _ingest(conn, _FakeParser(bank, ref, _rows(10000, 1 + 4 * i)), str(i))
        assert conn.execute("SELECT COUNT(*) FROM statements WHERE account_id IS NULL").fetchone()[0] == 0


class TestContinuityPerAccount:
    def test_two_accounts_at_one_bank_do_not_warn_about_each_other(self):
        conn = _make_conn()
        _ingest(conn, _FakeParser("kotak", "1111", _rows(10000, 1)), "a")
        # The second account opens at a balance that has nothing to do with the first's close.
        b = _ingest(conn, _FakeParser("kotak", "2222", _rows(500000, 10)), "b")
        assert check_continuity(conn, b) == []

    def test_a_gap_inside_one_account_still_warns(self):
        conn = _make_conn()
        _ingest(conn, _FakeParser("kotak", "1111", _rows(10000, 1)), "a")
        later = _ingest(conn, _FakeParser("kotak", "1111", _rows(50000, 10)), "b")
        warnings = check_continuity(conn, later)
        assert len(warnings) == 1
        assert "doesn't match" in warnings[0] and "may be missing" in warnings[0]

    def test_unnumbered_statements_join_the_one_account_and_chain(self):
        conn = _make_conn()
        _ingest(conn, _FakeParser("kotak", "1111", _rows(10000, 1)), "a")
        # Opens where the first closed (10000 - 300): no warning, same account.
        b = _ingest(conn, _FakeParser("kotak", None, _rows(9700, 10)), "b")
        assert check_continuity(conn, b) == []
        assert _account_count(conn) == 1


# --- the real fixtures -----------------------------------------------------


class TestFixtures:
    def test_the_two_fixture_statements_are_one_account_named_by_bank_and_digits(self):
        conn = _make_conn()
        oct_ = ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        nov = ingest_pdf(str(FIXTURES / "sample_statement_nov.pdf"), conn=conn)
        assert oct_.account_id == nov.account_id
        label = conn.execute(
            "SELECT bank_name, account_ref FROM accounts WHERE id = ?", (oct_.account_id,)
        ).fetchone()
        assert (label["bank_name"], label["account_ref"]) == ("generic", "XXXX0000")
        assert account_label(None, label["bank_name"], label["account_ref"]) == "Bank ··0000"

    def test_the_other_bank_fixture_is_its_own_account_with_a_full_month_printed(self):
        conn = _make_conn()
        first = ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        other = ingest_pdf(str(FIXTURES / "sample_statement_other_bank.pdf"), conn=conn)
        assert other.account_id != first.account_id
        row = conn.execute(
            "SELECT s.bank_name, s.period_start, s.period_end, s.printed_start, s.printed_end, "
            "a.bank_name AS account_bank, a.account_ref FROM statements s "
            "JOIN accounts a ON a.id = s.account_id WHERE s.id = ?", (other.id,)
        ).fetchone()
        assert row["bank_name"] == "hdfc (auto)"
        assert (row["account_bank"], row["account_ref"]) == ("hdfc", "XXXX4321")
        assert account_label(None, row["account_bank"], row["account_ref"]) == "HDFC ··4321"
        # The month is printed in full; the payments stop on the 12th.
        assert (row["printed_start"], row["printed_end"]) == ("2026-10-01", "2026-10-31")
        assert (row["period_start"], row["period_end"]) == ("2026-10-02", "2026-10-12")
        # Opening 60,000, six payments, balanced: no continuity warning against the first account.
        assert check_continuity(conn, other) == []


# --- the routes -------------------------------------------------------------


@pytest.fixture
def api():
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.main import app

    conn = _make_conn()
    app.dependency_overrides[api_get_db] = lambda: conn
    yield TestClient(app), conn
    app.dependency_overrides.pop(api_get_db, None)


def _two_accounts(conn):
    """Kotak ··1111 with two statements, HDFC ··2222 with one."""
    k1 = _ingest(conn, _FakeParser("kotak", "5551111", _rows(10000, 1)), "k1")
    k2 = _ingest(conn, _FakeParser("kotak", "5551111", _rows(9700, 10)), "k2")
    h1 = _ingest(conn, _FakeParser("hdfc (auto)", "XXXX2222", _rows(50000, 5, month=6), period=None), "h1")
    return k1, k2, h1


class TestAccountRoutes:
    def test_list_gives_label_counts_dates_and_the_latest_balance(self, api):
        client, conn = api
        k1, k2, h1 = _two_accounts(conn)
        body = client.get("/api/accounts").json()
        by_label = {a["account_label"]: a for a in body}
        assert set(by_label) == {"Kotak ··1111", "HDFC ··2222"}
        kotak = by_label["Kotak ··1111"]
        assert kotak["id"] == k1.account_id
        assert kotak["name"] is None
        assert kotak["bank_name"] == "kotak"
        assert kotak["account_ref_last4"] == "1111"
        assert kotak["statement_count"] == 2
        assert (kotak["first_date"], kotak["last_date"]) == ("2026-05-01", "2026-05-12")
        # The closing balance of the statement that ends last.
        assert kotak["balance"] == 9400
        assert kotak["balance_as_of"] == "2026-05-12"

    def test_the_full_number_is_never_in_the_list(self, api):
        client, conn = api
        _two_accounts(conn)
        text = client.get("/api/accounts").text
        assert "5551111" not in text
        assert "XXXX2222" not in text

    def test_rename_trims_and_the_label_follows(self, api):
        client, conn = api
        k1, _, _ = _two_accounts(conn)
        r = client.patch(f"/api/accounts/{k1.account_id}", json={"name": "  Kotak savings  "})
        assert r.status_code == 200
        assert r.json()["name"] == "Kotak savings"
        assert r.json()["account_label"] == "Kotak savings"
        labels = {s["account_label"] for s in client.get("/api/statements").json() if s["account_id"] == k1.account_id}
        assert labels == {"Kotak savings"}

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_name_clears_it_back_to_the_derived_label(self, api, blank):
        client, conn = api
        k1, _, _ = _two_accounts(conn)
        client.patch(f"/api/accounts/{k1.account_id}", json={"name": "Kotak savings"})
        r = client.patch(f"/api/accounts/{k1.account_id}", json={"name": blank})
        assert r.json()["name"] is None
        assert r.json()["account_label"] == "Kotak ··1111"

    def test_a_name_over_sixty_characters_is_refused(self, api):
        client, conn = api
        k1, _, _ = _two_accounts(conn)
        assert client.patch(f"/api/accounts/{k1.account_id}", json={"name": "x" * 60}).status_code == 200
        r = client.patch(f"/api/accounts/{k1.account_id}", json={"name": "x" * 61})
        assert r.status_code == 422
        assert "60" in r.json()["detail"]

    def test_renaming_an_unknown_account_is_404(self, api):
        client, _ = api
        assert client.patch("/api/accounts/nope", json={"name": "x"}).status_code == 404

    def test_the_statements_list_carries_the_account_and_the_printed_period(self, api):
        client, conn = api
        k1, _, h1 = _two_accounts(conn)
        rows = {s["id"]: s for s in client.get("/api/statements").json()}
        assert rows[k1.id]["account_id"] == k1.account_id
        assert rows[k1.id]["account_label"] == "Kotak ··1111"
        assert rows[h1.id]["account_label"] == "HDFC ··2222"
        assert "printed_start" in rows[k1.id] and "printed_end" in rows[k1.id]

    def test_move_a_statement_to_another_account(self, api):
        client, conn = api
        k1, k2, h1 = _two_accounts(conn)
        r = client.patch(f"/api/statements/{k2.id}", json={"account_id": h1.account_id})
        assert r.status_code == 200
        assert r.json()["account_id"] == h1.account_id
        assert r.json()["account_label"] == "HDFC ··2222"
        counts = {a["id"]: a["statement_count"] for a in client.get("/api/accounts").json()}
        assert counts == {k1.account_id: 1, h1.account_id: 2}

    def test_moving_the_last_statement_out_deletes_the_account(self, api):
        client, conn = api
        k1, k2, h1 = _two_accounts(conn)
        assert client.patch(f"/api/statements/{h1.id}", json={"account_id": k1.account_id}).status_code == 200
        ids = {a["id"] for a in client.get("/api/accounts").json()}
        assert ids == {k1.account_id}
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1

    def test_move_a_statement_to_a_new_account(self, api):
        """Splits a statement out of the account ingest put it in; the new account carries its
        bank and number, and the name when one is given."""
        client, conn = api
        k1, k2, h1 = _two_accounts(conn)
        r = client.patch(f"/api/statements/{k2.id}", json={"new_account": True, "name": "  Joint account "})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["account_id"] not in (k1.account_id, h1.account_id)
        assert body["account_label"] == "Joint account"
        accounts = {a["id"]: a for a in client.get("/api/accounts").json()}
        assert accounts[body["account_id"]]["statement_count"] == 1
        assert accounts[body["account_id"]]["bank_name"] == "kotak"
        assert accounts[body["account_id"]]["account_ref_last4"] == "1111"
        assert accounts[k1.account_id]["statement_count"] == 1
        stored = conn.execute("SELECT bank_name, account_ref, name FROM accounts WHERE id = ?", (body["account_id"],)).fetchone()
        assert tuple(stored) == ("kotak", "5551111", "Joint account")

    @pytest.mark.parametrize("body", [{}, {"name": None}, {"name": ""}, {"name": "   "}])
    def test_a_new_account_needs_a_name(self, api, body):
        """Two accounts of one bank and number would read the same everywhere, so a split
        without a name is refused in plain words and nothing changes."""
        client, conn = api
        k1, k2, _ = _two_accounts(conn)
        r = client.patch(f"/api/statements/{k2.id}", json={"new_account": True, **body})
        assert r.status_code == 422
        assert r.json()["detail"] == "Give the new account a name, so you can tell the two accounts apart."
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2
        assert conn.execute("SELECT account_id FROM statements WHERE id = ?", (k2.id,)).fetchone()[0] == k1.account_id

    def test_a_split_keeps_the_next_statement_of_that_bank_and_number_in_the_new_account(self, api):
        """The latest statement of the bank and number was moved: its account is where the next one goes."""
        client, conn = api
        k1, k2, _ = _two_accounts(conn)
        new_id = client.patch(f"/api/statements/{k2.id}", json={"new_account": True, "name": "Joint"}).json()["account_id"]
        k3 = _ingest(conn, _FakeParser("kotak", "5551111", _rows(9400, 20)), "k3")
        assert k3.account_id == new_id and k3.account_is_new is False
        assert conn.execute("SELECT COUNT(*) FROM accounts WHERE bank_name = 'kotak'").fetchone()[0] == 2

    def test_a_statement_alone_in_its_account_is_not_moved_to_a_new_one(self, api):
        client, conn = api
        _, _, h1 = _two_accounts(conn)
        r = client.patch(f"/api/statements/{h1.id}", json={"new_account": True, "name": "Joint"})
        assert r.status_code == 422
        assert conn.execute("SELECT account_id FROM statements WHERE id = ?", (h1.id,)).fetchone()[0] == h1.account_id
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2

    def test_a_new_account_name_is_capped(self, api):
        client, conn = api
        k1, k2, _ = _two_accounts(conn)
        r = client.patch(f"/api/statements/{k2.id}", json={"new_account": True, "name": "x" * 61})
        assert r.status_code == 422
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2

    def test_an_account_and_a_new_account_together_are_refused(self, api):
        client, conn = api
        k1, k2, h1 = _two_accounts(conn)
        assert client.patch(f"/api/statements/{k2.id}", json={"account_id": h1.account_id, "new_account": True}).status_code == 422
        assert client.patch(f"/api/statements/{k2.id}", json={}).status_code == 422

    def test_a_new_account_is_refused_while_the_statement_is_being_sorted(self, api):
        client, conn = api
        k1, k2, _ = _two_accounts(conn)
        conn.execute(
            "INSERT INTO annotation_jobs (id, status, statement_id, kind) VALUES ('j1', 'running', ?, 'annotate')",
            (k2.id,),
        )
        conn.commit()
        assert client.patch(f"/api/statements/{k2.id}", json={"new_account": True, "name": "Joint"}).status_code == 409
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2

    def test_moving_to_an_unknown_account_is_404_and_changes_nothing(self, api):
        client, conn = api
        k1, _, _ = _two_accounts(conn)
        r = client.patch(f"/api/statements/{k1.id}", json={"account_id": "nope"})
        assert r.status_code == 404
        assert conn.execute("SELECT account_id FROM statements WHERE id = ?", (k1.id,)).fetchone()[0] == k1.account_id

    def test_moving_an_unknown_statement_is_404(self, api):
        client, _ = api
        assert client.patch("/api/statements/nope", json={"account_id": "x"}).status_code == 404

    def test_moving_is_refused_while_the_statement_is_being_sorted(self, api):
        client, conn = api
        k1, _, h1 = _two_accounts(conn)
        conn.execute(
            "INSERT INTO annotation_jobs (id, status, statement_id, kind) VALUES ('j1', 'running', ?, 'annotate')",
            (k1.id,),
        )
        conn.commit()
        r = client.patch(f"/api/statements/{k1.id}", json={"account_id": h1.account_id})
        assert r.status_code == 409
        assert "being sorted" in r.json()["detail"]
        assert conn.execute("SELECT account_id FROM statements WHERE id = ?", (k1.id,)).fetchone()[0] == k1.account_id

    def test_the_old_bank_name_edit_is_gone(self, api):
        client, conn = api
        k1, _, _ = _two_accounts(conn)
        r = client.patch(f"/api/statements/{k1.id}", json={"bank_name": "Kotak savings"})
        assert r.status_code == 422
        assert conn.execute("SELECT bank_name FROM statements WHERE id = ?", (k1.id,)).fetchone()[0] == "kotak"

    def test_deleting_a_statement_keeps_an_account_that_still_has_one(self, api):
        client, conn = api
        k1, k2, h1 = _two_accounts(conn)
        assert client.delete(f"/api/statements/{k1.id}").status_code == 200
        assert {a["id"] for a in client.get("/api/accounts").json()} == {k1.account_id, h1.account_id}

    def test_deleting_an_accounts_last_statement_deletes_the_account(self, api):
        client, conn = api
        k1, k2, h1 = _two_accounts(conn)
        client.patch(f"/api/accounts/{h1.account_id}", json={"name": "Salary account"})
        assert client.delete(f"/api/statements/{h1.id}").status_code == 200
        assert {a["id"] for a in client.get("/api/accounts").json()} == {k1.account_id}
        assert conn.execute("SELECT COUNT(*) FROM accounts WHERE id = ?", (h1.account_id,)).fetchone()[0] == 0

    def test_the_balance_chart_names_the_account_it_draws(self, api):
        client, conn = api
        k1, _, _ = _two_accounts(conn)
        client.patch(f"/api/accounts/{k1.account_id}", json={"name": "Kotak savings"})
        body = client.get("/api/insights").json()
        assert body["balance"]["account"] == "Kotak savings"


class TestUploadResponse:
    def test_the_upload_says_which_account_and_whether_it_is_new(self, api):
        client, conn = api
        parser = _FakeParser("kotak", "5551111", _rows(10000, 1))
        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=parser), \
                patch("finance_copilot.api.routes.annotations._run_annotation_job"):
            first = client.post("/api/statements/upload", files={"file": ("a.pdf", b"%PDF one", "application/pdf")})
            second = client.post("/api/statements/upload", files={"file": ("b.pdf", b"%PDF two", "application/pdf")})
        assert first.status_code == 200, first.text
        a, b = first.json(), second.json()
        assert a["account_is_new"] is True and b["account_is_new"] is False
        assert a["account_id"] == b["account_id"]
        assert a["account_label"] == b["account_label"] == "Kotak ··1111"

    def test_neither_the_upload_nor_the_list_carries_the_full_account_number(self, api):
        client, conn = api
        parser = _FakeParser("kotak", "5551111", _rows(10000, 1))
        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=parser), \
                patch("finance_copilot.api.routes.annotations._run_annotation_job"):
            uploaded = client.post(
                "/api/statements/upload", files={"file": ("a.pdf", b"%PDF one", "application/pdf")}
            ).json()
        listed = client.get("/api/statements").json()[0]
        for body in (uploaded, listed):
            assert "account_ref" not in body
            assert body["account_ref_last4"] == "1111"
            assert "5551111" not in str(body)

    def test_the_duplicate_message_names_the_account_not_the_parser_key(self, api):
        client, conn = api
        parser = _FakeParser("hdfc (auto)", "XXXX2222", _rows(10000, 1))
        files = {"file": ("a.pdf", b"%PDF same", "application/pdf")}
        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=parser), \
                patch("finance_copilot.api.routes.annotations._run_annotation_job"):
            client.post("/api/statements/upload", files=files)
            again = client.post("/api/statements/upload", files=files)
        assert again.status_code == 409
        assert "HDFC ··2222" in again.json()["detail"]
        assert "(auto)" not in again.json()["detail"]


class TestTheSameFileAtOnce:
    def test_two_identical_uploads_at_once_give_one_statement_and_one_duplicate_refusal(self, tmp_path):
        """Both pass the "already added?" look before either writes; the loser is a
        duplicate (the page's 409), not a foreign-key crash."""
        import threading

        from finance_copilot.db.connection import get_connection
        from finance_copilot.pipeline.ingest import DuplicateStatementError

        db = str(tmp_path / "ledger.db")
        init_db(get_connection(db))
        pdf = tmp_path / "s.pdf"
        pdf.write_bytes(b"%PDF same file")
        parser = _FakeParser("kotak", "1111", _rows(10000, 1))
        both_looked = threading.Barrier(2)
        original_detect = parser.detect

        def detect(path, password=None):
            both_looked.wait(timeout=10)  # after the duplicate look, before the write
            return original_detect(path, password)

        parser.detect = detect
        results: list = []

        def upload():
            conn = get_connection(db)
            try:
                with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=parser):
                    results.append(ingest_pdf(str(pdf), conn=conn))
            except Exception as exc:  # noqa: BLE001
                results.append(exc)
            finally:
                conn.close()

        threads = [threading.Thread(target=upload) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        errors = [r for r in results if isinstance(r, Exception)]
        assert len(results) == 2
        assert len(errors) == 1 and isinstance(errors[0], DuplicateStatementError), results
        check = get_connection(db)
        assert check.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 1
        assert check.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
