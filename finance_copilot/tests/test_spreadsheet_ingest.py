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
from finance_copilot.pipeline import spreadsheet_ingest as _ingest
from finance_copilot.pipeline.ingest import (
    DuplicateStatementError,
    NothingNewError,
    OverlapNeedsConfirmation,
    ingest_pdf,
)
from finance_copilot.pipeline.spreadsheet_ingest import NeedsAnswer

FIXTURES = Path(__file__).parent / "fixtures"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def ingest_spreadsheet(conn, contents, **answers):
    """The ingest, with the dates read day first where a file of early-month days
    leaves it open (the fixtures' days stop at the 8th or the 12th): what these tests
    are about is not that question, which TestWhichWayRoundTheDatesGo asks."""
    answers.setdefault("date_order", "dmy")
    return _ingest.ingest_spreadsheet(conn, contents, **answers)


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


class TestOneAccountFromEitherFile:
    """The same account's CSV (masked number, no bank named) and PDF (full number,
    bank known) are one account, in either order."""

    CSV = (
        b"Statement of account\nAccount No,XXXXXX4321\n\nDate,Narration,Withdrawal Amt.,Deposit Amt.,Balance\n"
        b"01/10/2026,UPI/A/1,100.00,,900.00\n02/10/2026,UPI/B/2,,50.00,950.00\n"
    )

    def _pdf_like(self, conn, ref, bank="kotak"):
        from finance_copilot.db.queries.accounts import resolve_account

        return resolve_account(conn, bank, ref)

    def test_a_csv_then_the_same_accounts_pdf(self):
        conn = _conn()
        got = ingest_spreadsheet(conn, self.CSV, new_account_name="Kotak savings")
        account, created = self._pdf_like(conn, "1234564321")
        assert (account, created) == (got.account_id, False)
        assert [r["bank_name"] for r in conn.execute("SELECT bank_name FROM accounts")] == ["kotak"]

    def test_a_pdf_then_the_same_accounts_csv(self):
        conn = _conn()
        account, _ = self._pdf_like(conn, "1234564321")
        conn.execute("INSERT INTO statements (id, bank_name, parser_version, statement_month, account_id) VALUES ('s0', 'kotak', 'x', '2026-09', ?)", (account,))
        conn.commit()
        got = ingest_spreadsheet(conn, self.CSV)
        assert got.account_id == account and not got.account_is_new

    def test_two_accounts_that_match_the_masked_number_ask_which(self):
        conn = _conn()
        a, _ = self._pdf_like(conn, "1234564321")
        b, _ = self._pdf_like(conn, "9999994321")
        for acc, sid in ((a, "s1"), (b, "s2")):
            conn.execute("INSERT INTO statements (id, bank_name, parser_version, statement_month, account_id) VALUES (?, 'kotak', 'x', '2026-09', ?)", (sid, acc))
        conn.commit()
        with pytest.raises(NeedsAnswer) as asked:
            ingest_spreadsheet(conn, self.CSV)
        assert asked.value.needs == ["account"]
        assert nothing_new_stored(conn)

    def test_a_csv_of_unknown_bank_takes_the_known_bank_when_the_next_file_names_it(self):
        conn = _conn()
        ingest_spreadsheet(conn, self.CSV, new_account_name="Savings")
        assert [r["bank_name"] for r in conn.execute("SELECT bank_name FROM accounts")] == ["generic"]
        account, created = self._pdf_like(conn, "1234564321")
        assert created is False
        assert [r["bank_name"] for r in conn.execute("SELECT bank_name FROM accounts")] == ["kotak"]


def nothing_new_stored(conn) -> bool:
    return count(conn, "statements") == 2 and count(conn, "transactions") == 0


NAMED_EARLY = (
    "Example Bank\nAccount Number,XXXX0000\n\nDate,Narration,Withdrawal Amt.,Deposit Amt.,Balance\n"
    "01/10/2026,UPI/A/1,100.00,,24900.00\n02/10/2026,UPI/B/2,,50.00,24950.00\n03/10/2026,UPI/C/3,10.00,,24940.00\n"
)


class TestWhichWayRoundTheDatesGo:
    """A file whose days are all 12 or less, and whose order nothing settles, is asked
    about, even when it names its account and its header is known. The answer is kept
    with the layout."""

    def _with_account(self):
        conn = _conn()
        pdf = ingest_pdf(str(FIXTURES / "sample_statement.pdf"), conn=conn)  # XXXX0000
        return conn, pdf

    def test_a_file_that_names_its_account_is_still_asked_and_stores_nothing(self):
        conn, pdf = self._with_account()
        before = {t: count(conn, t) for t in ("statements", "transactions", "accounts", "import_layouts")}
        with pytest.raises(NeedsAnswer) as asked:
            _ingest.ingest_spreadsheet(conn, NAMED_EARLY.encode())
        body = asked.value.body
        assert asked.value.needs == ["dates"]
        assert body["account_id"] == pdf.account_id  # the form starts on the account the file names
        assert body["date_order"] == "dmy"
        assert set(body["parsed"]["date_choices"]) == {"dmy", "mdy"}
        assert body["parsed"]["date_choices"]["mdy"]["rows"][0]["date"] == "2026-01-10"
        assert {t: count(conn, t) for t in before} == before

    def test_the_answer_decides_how_the_dates_read_and_is_remembered_with_the_layout(self):
        conn, pdf = self._with_account()
        got = _ingest.ingest_spreadsheet(conn, NAMED_EARLY.encode(), date_order="mdy")
        assert got.account_id == pdf.account_id
        days = [r[0] for r in conn.execute("SELECT txn_date FROM transactions WHERE statement_id = ? ORDER BY txn_date", (got.id,))]
        assert days == ["2026-01-10", "2026-02-10", "2026-03-10"]
        layout = conn.execute("SELECT date_order FROM import_layouts").fetchone()
        assert layout["date_order"] == "mdy"

    def test_the_next_file_with_the_header_is_read_the_same_way_without_asking(self):
        conn, _ = self._with_account()
        _ingest.ingest_spreadsheet(conn, NAMED_EARLY.encode(), date_order="mdy")
        later = NAMED_EARLY.replace("01/10", "04/10").replace("02/10", "05/10").replace("03/10", "06/10")
        got = _ingest.ingest_spreadsheet(conn, later.encode())  # asks nothing
        assert conn.execute("SELECT MIN(txn_date) FROM transactions WHERE statement_id = ?", (got.id,)).fetchone()[0] == "2026-04-10"

    def test_a_file_that_says_its_own_order_is_not_overruled_by_the_remembered_one(self):
        conn, _ = self._with_account()
        _ingest.ingest_spreadsheet(conn, NAMED_EARLY.encode(), date_order="mdy")
        settled = NAMED_EARLY.replace("03/10/2026", "25/10/2026")
        got = _ingest.ingest_spreadsheet(conn, settled.encode(), confirm_overlap=True)
        assert conn.execute("SELECT MAX(txn_date) FROM transactions WHERE statement_id = ?", (got.id,)).fetchone()[0] == "2026-10-25"
        # ... and it did not forget the answer the person gave.
        assert conn.execute("SELECT date_order FROM import_layouts").fetchone()["date_order"] == "mdy"

    def test_an_unknown_header_with_ambiguous_dates_asks_for_the_columns_the_account_and_the_dates(self):
        conn = _conn()
        text = "When,What,Moved,Left\n01/10/2026,UPI/A/1,-100.00,900.00\n02/10/2026,UPI/B/2,50.00,950.00\n"
        with pytest.raises(NeedsAnswer) as asked:
            _ingest.ingest_spreadsheet(conn, text.encode())
        assert asked.value.needs == ["mapping", "account", "dates"]

    def test_a_wrong_date_order_is_refused_in_words(self):
        conn, _ = self._with_account()
        with pytest.raises(ValueError, match="day first or month first"):
            _ingest.ingest_spreadsheet(conn, NAMED_EARLY.encode(), date_order="ymd")

    def test_a_file_that_names_its_account_but_not_its_columns_starts_the_form_on_that_account(self):
        conn, pdf = self._with_account()
        text = "Account Number,XXXX0000\n\nWhen,What,Moved,Left\n2026-10-01,UPI/A/1,-100.00,900.00\n2026-10-02,UPI/B/2,50.00,950.00\n"
        with pytest.raises(NeedsAnswer) as asked:
            _ingest.ingest_spreadsheet(conn, text.encode())
        assert asked.value.needs == ["mapping"]
        assert asked.value.body["account_id"] == pdf.account_id

    def test_a_file_with_no_header_row_never_goes_to_an_account_by_its_layout(self):
        conn = _conn()
        text = "2026-10-01,UPI/A/1,-100.00,900.00\n2026-10-02,UPI/B/2,50.00,950.00\n2026-10-03,UPI/C/3,-5.00,945.00\n"
        mapping = json.dumps({"date": 0, "description": 1, "amount": 2, "balance": 3})
        first = _ingest.ingest_spreadsheet(conn, text.encode(), mapping=mapping, new_account_name="Savings")
        assert conn.execute("SELECT account_id FROM import_layouts").fetchone()["account_id"] is None
        again = "2026-10-04,UPI/D/4,-5.00,940.00\n2026-10-05,UPI/E/5,-5.00,935.00\n"
        with pytest.raises(NeedsAnswer) as asked:
            _ingest.ingest_spreadsheet(conn, again.encode())
        assert asked.value.needs == ["account"]  # the columns are remembered, the account is asked
        assert first.account_id
