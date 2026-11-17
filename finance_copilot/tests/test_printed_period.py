"""The period a statement prints: reading it, and keeping it only when it is sane."""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from finance_copilot.db.connection import init_db
from finance_copilot.models.transaction import Transaction
from finance_copilot.parsers.banks.kotak import KotakParser
from finance_copilot.parsers.base import StatementParser
from finance_copilot.parsers.period import parse_printed_period
from finance_copilot.pipeline.ingest import ingest_pdf

FIXTURES = Path(__file__).parent / "fixtures"


class TestParsePrintedPeriod:
    @pytest.mark.parametrize("text,expected", [
        # The fixture statements' header.
        ("Example Bank\nAccount: XXXX0000   Period: 01/10/2026 - 08/10/2026   Opening balance: 25,000.00\n",
         (date(2026, 10, 1), date(2026, 10, 8))),
        ("Statement Period: 01/10/2026 to 31/10/2026", (date(2026, 10, 1), date(2026, 10, 31))),
        ("Statement period 01-Oct-2026 To 31-Oct-2026", (date(2026, 10, 1), date(2026, 10, 31))),
        ("From 01 Oct 2026 To 31 Oct 2026", (date(2026, 10, 1), date(2026, 10, 31))),
        ("Period: 01.10.2026 - 31.10.2026", (date(2026, 10, 1), date(2026, 10, 31))),
        ("For the period 1st October 2026 to 31st October 2026", (date(2026, 10, 1), date(2026, 10, 31))),
        ("Statement for the period 2026-10-01 to 2026-10-31", (date(2026, 10, 1), date(2026, 10, 31))),
        # Kotak's legacy layout: day, abbreviated month with a comma, year.
        ("Account Statement\n01 Feb, 2026 - 28 Feb, 2026\nKotak Mahindra Bank",
         (date(2026, 2, 1), date(2026, 2, 28))),
        # Kotak's current layout: no comma.
        ("Account Statement\nTest User Account No. 1234567890\n01 Jul 2026 - 31 Jul 2026\nIFSC Code KKBK0000123",
         (date(2026, 7, 1), date(2026, 7, 31))),
        ("Statement period: 1 Jan 2026 – 31 Mar 2026", (date(2026, 1, 1), date(2026, 3, 31))),
        ("Period: 01/10/26 - 31/10/26", (date(2026, 10, 1), date(2026, 10, 31))),
        ("Statement between 01/10/2026 and 31/10/2026", (date(2026, 10, 1), date(2026, 10, 31))),
    ])
    def test_the_common_forms(self, text, expected):
        assert parse_printed_period(text) == expected

    def test_a_long_dash_between_the_dates(self):
        text = "Period: 01/10/2026 " + chr(0x2014) + " 31/10/2026"
        assert parse_printed_period(text) == (date(2026, 10, 1), date(2026, 10, 31))

    @pytest.mark.parametrize("text", [
        "",
        "Statement of account for May",
        "Opening balance 25,000.00 closing balance 30,000.00",
        "Period: 31/10/2026 - 01/10/2026",  # backwards: not a period
        "Period: 32/13/2026 - 40/14/2026",  # not dates
        "Date of issue: 05/11/2026",  # one date is not a period
    ])
    def test_it_says_nothing_when_the_header_says_nothing(self, text):
        assert parse_printed_period(text) is None

    def test_dates_in_the_payment_table_are_not_the_period(self):
        text = "Account Statement\nDate Narration Withdrawal Deposit Balance\n01/10/2026 UPI/X 5.00\n02/10/2026 - 03/10/2026"
        assert parse_printed_period(text) is None

    def test_a_labelled_period_beats_another_pair_of_dates(self):
        text = "Printed 05/11/2026 - 05/11/2026\nStatement period: 01/10/2026 to 31/10/2026"
        assert parse_printed_period(text) == (date(2026, 10, 1), date(2026, 10, 31))


class TestExtractPeriod:
    def test_the_fixture_statements_yield_their_period(self):
        parser = KotakParser()  # any parser: the reading is shared
        assert parser.extract_period(str(FIXTURES / "sample_statement.pdf")) == (date(2026, 10, 1), date(2026, 10, 8))
        assert parser.extract_period(str(FIXTURES / "sample_statement_nov.pdf")) == (date(2026, 11, 1), date(2026, 11, 8))

    def test_an_unreadable_file_is_none_and_does_not_raise(self):
        assert KotakParser().extract_period("/nonexistent/file.pdf") is None

    def test_the_first_page_text_is_what_is_read(self):
        page = MagicMock()
        page.extract_text.return_value = "Statement period: 01/10/2026 to 31/10/2026"
        pdf = MagicMock()
        pdf.pages = [page]
        pdf.__enter__ = lambda s: pdf
        pdf.__exit__ = lambda s, *a: False
        with patch("pdfplumber.open", return_value=pdf):
            assert KotakParser().extract_period("x.pdf") == (date(2026, 10, 1), date(2026, 10, 31))


# --- ingest keeps it only when it is sane ---------------------------------


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


class _Parser(StatementParser):
    bank_name = "kotak"
    version = "1"

    def __init__(self, period):
        self._period = period

    def detect(self, path, password=None):
        return True

    def parse(self, path, password=None):
        return [
            Transaction(txn_date=date(2026, 10, 5), amount=10.0, debit_credit="debit", raw_description="A"),
            Transaction(txn_date=date(2026, 10, 28), amount=10.0, debit_credit="debit", raw_description="B"),
        ]

    def extract_period(self, path, password=None):
        return self._period


def _ingest(period):
    conn = _make_conn()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"%PDF period")
        path = tmp.name
    try:
        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=_Parser(period)):
            statement = ingest_pdf(path, conn=conn)
    finally:
        Path(path).unlink()
    row = conn.execute(
        "SELECT period_start, period_end, printed_start, printed_end FROM statements WHERE id = ?",
        (statement.id,),
    ).fetchone()
    return statement, dict(row)


class TestIngestKeepsASanePrintedPeriod:
    def test_a_full_month_is_kept_beside_the_payment_dates(self):
        statement, row = _ingest((date(2026, 10, 1), date(2026, 10, 31)))
        assert row == {
            "period_start": "2026-10-05", "period_end": "2026-10-28",  # payment dates do not move
            "printed_start": "2026-10-01", "printed_end": "2026-10-31",
        }
        assert statement.printed_start == date(2026, 10, 1)

    def test_a_period_that_leaves_out_a_payment_is_dropped_and_logged_once(self, caplog):
        with caplog.at_level("WARNING"):
            _, row = _ingest((date(2026, 10, 1), date(2026, 10, 20)))
        assert row["printed_start"] is None and row["printed_end"] is None
        assert sum("printed period" in r.message for r in caplog.records) == 1

    def test_a_statement_that_prints_no_period_stores_none(self):
        _, row = _ingest(None)
        assert row["printed_start"] is None and row["printed_end"] is None

    def test_a_period_exactly_the_payment_dates_is_sane(self):
        _, row = _ingest((date(2026, 10, 5), date(2026, 10, 28)))
        assert row["printed_start"] == "2026-10-05"
