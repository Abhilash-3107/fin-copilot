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
        ("Kotak savings", None, "Kotak savings"),
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

    def test_an_account_gets_a_ulid_style_id(self):
        conn = _make_conn()
        account_id, _ = resolve_account(conn, "kotak", "1")
        assert account_id.startswith("acc_") and len(account_id) > 10


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
