"""A spreadsheet goes through the same ingest as a PDF, and asks only when it must.

Ingest tests over an in-memory ledger: the account a file belongs to (the number
above its table, a remembered layout, or the person's choice), the one-time
question about the columns, what a refused or unanswered file leaves behind
(nothing), and the dedup of a CSV against a PDF of the same month.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path

import pytest

from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.accounts import delete_empty_accounts
from finance_copilot.db.queries.import_layouts import get_layout
from finance_copilot.parsers.spreadsheet import SpreadsheetError
from finance_copilot.pipeline.ingest import (
    DuplicateStatementError,
    NothingNewError,
    OverlapNeedsConfirmation,
    ingest_pdf,
)
from finance_copilot.pipeline.spreadsheet_ingest import NeedsAnswer, ingest_spreadsheet

FIXTURES = Path(__file__).parent / "fixtures"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def count(conn, table) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def nothing_stored(conn) -> bool:
    return all(count(conn, t) == 0 for t in ("statements", "transactions", "accounts", "import_layouts"))


EXPORT_MAPPING = {"date": 0, "description": 1, "amount": 2, "balance": 3}


class TestAKnownAccount:
    def test_a_number_above_the_table_that_an_account_has_lands_with_no_questions(self):
        conn = _conn()
        pdf = ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        got = ingest_spreadsheet(conn, fixture("sample_statement_nov.csv"))
        assert got.account_id == pdf.account_id
        assert not got.account_is_new
        assert (got.rows_in_file, got.rows_added) == (8, 8)
        assert count(conn, "accounts") == 1
        assert got.parser_version.startswith("spreadsheet")
        assert got.bank_name == pdf.bank_name

    def test_the_same_rows_as_a_pdf_are_the_same_payments(self):
        """The CSV prints the same payments with different narrations: the balances
        say they are the ones the PDF already added."""
        conn = _conn()
        ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        text = fixture("sample_statement.csv").decode()
        for old, new in (("UPI/SWIGGY/ORDER/400112233/UPI", "Swiggy"), ("NEFT/ACME LABS PVT LTD/SALARY OCT", "Salary")):
            text = text.replace(old, new)
        with pytest.raises(NothingNewError):
            ingest_spreadsheet(conn, text.encode())
        assert count(conn, "statements") == 1 and count(conn, "transactions") == 8

    def test_the_overlap_csv_after_the_months_adds_only_what_is_new(self):
        conn = _conn()
        ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        ingest_pdf(str(FIXTURES / "sample_statement_nov.pdf"), conn=conn)
        got = ingest_spreadsheet(conn, fixture("sample_statement_overlap.csv"))
        assert (got.rows_in_file, got.rows_added) == (4 + 8 + 3, 3)
        assert count(conn, "transactions") == 8 + 8 + 3

    def test_the_overlap_csv_first_then_the_months_adds_what_each_lacks(self):
        conn = _conn()
        ingest_spreadsheet(conn, fixture("sample_statement_overlap.csv"), new_account_name="Main")
        a = ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        assert a.rows_added == 4  # 1 to 4 October; the 5th on is in the CSV
        with pytest.raises(NothingNewError):  # November is all in the CSV already
            ingest_pdf(str(FIXTURES / "sample_statement_nov.pdf"), conn=conn)
        assert count(conn, "transactions") == 8 + 8 + 3

    def test_a_file_with_a_balance_records_its_check_and_one_without_records_none(self):
        conn = _conn()
        got = ingest_spreadsheet(conn, fixture("sample_statement.csv"), new_account_name="Main")
        assert got.balance_checked == 7 and got.balance_consistent == 7
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        for row in csv.reader(io.StringIO(fixture("sample_statement_nov.csv").decode())):
            writer.writerow(row[:4] if len(row) >= 5 else row)
        again = ingest_spreadsheet(conn, out.getvalue().encode())
        assert again.balance_checked == 0
        assert again.opening_balance is None

    def test_an_xlsx_and_an_xls_of_a_month_land_like_its_csv(self):
        for name in ("sample_statement.xlsx", "sample_statement.xls"):
            conn = _conn()
            got = ingest_spreadsheet(conn, fixture(name), new_account_name="Main")
            assert got.rows_added == 8, name
            assert str(got.printed_start) == "2026-10-01" and str(got.printed_end) == "2026-10-08", name

    def test_the_same_file_twice_is_refused(self):
        conn = _conn()
        ingest_spreadsheet(conn, fixture("sample_statement.csv"), new_account_name="Main")
        with pytest.raises(DuplicateStatementError):
            ingest_spreadsheet(conn, fixture("sample_statement.csv"))


class TestAskingOnce:
    def test_a_file_with_no_account_asks_for_one_and_stores_nothing(self):
        conn = _conn()
        with pytest.raises(NeedsAnswer) as asked:
            ingest_spreadsheet(conn, fixture("sample_statement.csv"))
        assert asked.value.needs == ["account"]
        assert nothing_stored(conn)

    def test_a_header_nothing_recognises_asks_for_the_columns_and_the_account(self):
        conn = _conn()
        ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        with pytest.raises(NeedsAnswer) as asked:
            ingest_spreadsheet(conn, fixture("sample_export_oct.csv"))
        body = asked.value.body
        assert body["needs"] == ["mapping", "account"]
        assert body["columns"] == ["When", "What", "Moved", "Left"]
        assert len(body["preview"]) == 5 and body["preview"][0][0] == "2026-10-02"
        assert body["guess"] == EXPORT_MAPPING
        assert body["parsed"]["error"] is None and body["parsed"]["rows"][0]["direction"] == "debit"
        assert [a["account_label"] for a in body["accounts"]] == ["Bank ··" + "0000"] or len(body["accounts"]) == 1
        assert "detail" in body and "passwords" not in body["detail"]
        assert count(conn, "statements") == 1  # only the PDF

    def test_the_answer_lands_the_file_in_a_new_account_and_remembers_the_layout(self):
        conn = _conn()
        got = ingest_spreadsheet(
            conn, fixture("sample_export_oct.csv"),
            mapping=json.dumps(EXPORT_MAPPING), new_account_name="Savings 2",
        )
        assert got.account_is_new
        assert conn.execute("SELECT name FROM accounts WHERE id = ?", (got.account_id,)).fetchone()[0] == "Savings 2"
        assert got.rows_added == 6
        layouts = conn.execute("SELECT signature, mapping, account_id FROM import_layouts").fetchall()
        assert len(layouts) == 1 and layouts[0]["account_id"] == got.account_id
        assert json.loads(layouts[0]["mapping"]) == EXPORT_MAPPING

    def test_the_next_file_with_the_same_header_asks_nothing(self):
        conn = _conn()
        first = ingest_spreadsheet(
            conn, fixture("sample_export_oct.csv"), mapping=json.dumps(EXPORT_MAPPING), new_account_name="Savings 2"
        )
        second = ingest_spreadsheet(conn, fixture("sample_export_nov.csv"))
        assert second.account_id == first.account_id and not second.account_is_new
        assert second.rows_added == 4
        assert count(conn, "import_layouts") == 1

    def test_an_existing_account_can_be_chosen(self):
        conn = _conn()
        pdf = ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        got = ingest_spreadsheet(
            conn, fixture("sample_export_oct.csv"), mapping=json.dumps(EXPORT_MAPPING),
            account_id=pdf.account_id, confirm_overlap=True,
        )
        assert got.account_id == pdf.account_id and count(conn, "accounts") == 1

    def test_a_chosen_account_that_is_gone_is_refused_in_words(self):
        conn = _conn()
        with pytest.raises(ValueError, match="isn't in your ledger"):
            ingest_spreadsheet(conn, fixture("sample_statement.csv"), account_id="acc_nope")
        assert nothing_stored(conn)

    def test_a_remembered_account_that_is_gone_asks_for_the_account_only(self):
        conn = _conn()
        first = ingest_spreadsheet(
            conn, fixture("sample_export_oct.csv"), mapping=json.dumps(EXPORT_MAPPING), new_account_name="Savings 2"
        )
        conn.execute("DELETE FROM transactions WHERE statement_id = ?", (first.id,))
        conn.execute("DELETE FROM statements WHERE id = ?", (first.id,))
        assert delete_empty_accounts(conn) == 1  # the delete does not fail on the layout
        assert get_layout(conn, conn.execute("SELECT signature FROM import_layouts").fetchone()[0])["account_id"] is None
        with pytest.raises(NeedsAnswer) as asked:
            ingest_spreadsheet(conn, fixture("sample_export_nov.csv"))
        assert asked.value.needs == ["account"]
        assert asked.value.body["guess"] == EXPORT_MAPPING

    def test_a_remembered_layout_never_overrides_a_number_above_the_table(self):
        """The layout went to one account; the file names another account's number."""
        conn = _conn()
        pdf = ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)  # XXXX0000
        other = ingest_pdf(str(FIXTURES / "sample_statement_other_bank.pdf"), conn=conn)  # XXXX4321
        ingest_spreadsheet(
            conn, fixture("sample_statement.csv"), account_id=other.account_id, confirm_overlap=True
        )  # remembered: the other account
        got = ingest_spreadsheet(conn, fixture("sample_statement_nov.csv"))  # same header, names XXXX0000
        assert got.account_id == pdf.account_id

    def test_a_number_that_matches_nothing_and_a_remembered_account_with_another_number_asks(self):
        conn = _conn()
        pdf = ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        ingest_spreadsheet(conn, fixture("sample_statement_nov.csv").replace(b"XXXX0000", b"XXXX9999"), account_id=pdf.account_id)
        text = fixture("sample_statement.csv").replace(b"XXXX0000", b"XXXX7777")
        with pytest.raises(NeedsAnswer) as asked:
            ingest_spreadsheet(conn, text)
        assert asked.value.needs == ["account"]

    def test_a_wrong_choice_is_refused_with_the_row_and_stores_nothing(self):
        conn = _conn()
        with pytest.raises(SpreadsheetError, match="running balance"):
            ingest_spreadsheet(
                conn, fixture("sample_export_oct.csv"),
                mapping=json.dumps({"date": 0, "description": 1, "debit": 3, "balance": 2}), new_account_name="X",
            )
        assert nothing_stored(conn)

    def test_a_new_account_needs_a_name(self):
        with pytest.raises(ValueError, match="name"):
            ingest_spreadsheet(_conn(), fixture("sample_statement.csv"), new_account_name="  ")


class TestAMonthAlreadyAddedAsAPdf:
    @staticmethod
    def _csv_without_balance_and_with_cut_narration() -> bytes:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        for row in csv.reader(io.StringIO(fixture("sample_statement.csv").decode())):
            if len(row) >= 5 and row[0][:2].isdigit():
                row = [row[0], row[1][:10], *row[2:4]]
            elif row[:1] == ["Date"]:
                row = row[:4]
            writer.writerow(row)
        return out.getvalue().encode()

    def test_a_copy_that_matches_nothing_is_held_before_anything_is_added(self):
        conn = _conn()
        ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        with pytest.raises(OverlapNeedsConfirmation) as held:
            ingest_spreadsheet(conn, self._csv_without_balance_and_with_cut_narration())
        assert held.value.rows_in_overlap == 8 and held.value.unmatched == 8
        assert count(conn, "statements") == 1 and count(conn, "transactions") == 8
        assert count(conn, "import_layouts") == 0

    def test_the_person_can_add_it_anyway(self):
        conn = _conn()
        ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        got = ingest_spreadsheet(
            conn, self._csv_without_balance_and_with_cut_narration(), confirm_overlap=True
        )
        assert got.rows_added == 8 and count(conn, "transactions") == 16
        assert got.notices  # the file covers dates already in the account and none of it matches

    def test_a_pdf_is_never_held(self):
        """The hold is for spreadsheets: a PDF keeps the notice it always had."""
        conn = _conn()
        ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        got = ingest_pdf(str(FIXTURES / "sample_statement_other_bank.pdf"), conn=conn)
        assert got.rows_added == 6

    def test_a_file_that_mostly_matches_is_not_held(self):
        conn = _conn()
        ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)
        ingest_pdf(str(FIXTURES / "sample_statement_nov.pdf"), conn=conn)
        got = ingest_spreadsheet(conn, fixture("sample_statement_overlap.csv"))
        assert got.rows_added == 3
