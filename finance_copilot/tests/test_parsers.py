"""Unit tests for bank statement parsers and registry behaviour."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from finance_copilot.models.transaction import Transaction
from finance_copilot.parsers.banks.kotak import KotakParser
from finance_copilot.parsers.registry import detect_parser, get_parser

REAL_PDF = Path("/Users/abhilashbora/Projects/finance-copilot/data/26356222-XXXXXXX-400097.pdf")
_PASSWORD = os.environ.get("KOTAK_PDF_PASSWORD")

# ---------------------------------------------------------------------------
# Unit tests — no PDF required
# ---------------------------------------------------------------------------


class TestKotakParserHelpers:
    def setup_method(self):
        self.parser = KotakParser()

    def test_is_transaction_row_valid(self):
        row = ["01 Feb, 2026", "UPI/Agoda Company\nP/118030236405", "REF123", "-1,234.56", "", "45,678.90"]
        assert self.parser._is_transaction_row(row) is True

    def test_is_transaction_row_single_digit_day(self):
        row = ["3 Mar, 2025", "UPI/TEST", "REF", "-100.00", "", "500.00"]
        assert self.parser._is_transaction_row(row) is True

    def test_is_transaction_row_rejects_header(self):
        row = ["DATE", "TRANSACTION DETAILS", "CHEQUE/\nREFERENCE#", "DEBIT", "CREDIT", "BALANCE"]
        assert self.parser._is_transaction_row(row) is False

    def test_is_transaction_row_rejects_blank(self):
        assert self.parser._is_transaction_row(["", "", "", "", "", ""]) is False

    def test_is_transaction_row_rejects_short_row(self):
        assert self.parser._is_transaction_row(["01 Feb, 2026"]) is False

    def test_parse_date_standard(self):
        assert self.parser._parse_date("01 Feb, 2026") == date(2026, 2, 1)

    def test_parse_date_single_digit_day(self):
        assert self.parser._parse_date("3 Mar, 2025") == date(2025, 3, 3)

    def test_parse_amount_debit_with_minus(self):
        assert self.parser._parse_amount("-1,234.56") == pytest.approx(1234.56)

    def test_parse_amount_credit_with_plus(self):
        assert self.parser._parse_amount("+2,600.00") == pytest.approx(2600.00)

    def test_parse_amount_no_sign(self):
        assert self.parser._parse_amount("2,000.00") == pytest.approx(2000.00)

    def test_parse_amount_indian_lakh_format(self):
        assert self.parser._parse_amount("+1,30,000.00") == pytest.approx(130000.00)

    def test_parse_balance_standard(self):
        assert self.parser._parse_balance("45,678.90") == pytest.approx(45678.90)

    def test_parse_balance_empty_returns_none(self):
        assert self.parser._parse_balance("") is None

    def test_parse_balance_whitespace_returns_none(self):
        assert self.parser._parse_balance("   ") is None

    def test_clean_text_replaces_newline(self):
        assert self.parser._clean_text("UPI/Agoda Company\nP/118030236405") == "UPI/Agoda Company P/118030236405"

    def test_clean_text_strips_whitespace(self):
        assert self.parser._clean_text("  Hello World  ") == "Hello World"

    def test_clean_text_no_change_when_clean(self):
        assert self.parser._clean_text("UPI/TEST") == "UPI/TEST"


class TestRowToTransaction:
    def setup_method(self):
        self.parser = KotakParser()

    def test_debit_row(self):
        row = ["01 Feb, 2026", "UPI/Agoda Company\nP/118030236405", "REF123\n305", "-1,234.56", "", "45,678.90"]
        txn = self.parser._row_to_transaction(row)
        assert txn.debit_credit == "debit"
        assert txn.amount == pytest.approx(1234.56)
        assert txn.txn_date == date(2026, 2, 1)
        assert txn.raw_description == "UPI/Agoda Company P/118030236405"
        assert txn.running_balance == pytest.approx(45678.90)

    def test_credit_row(self):
        row = ["15 Feb, 2026", "SALARY CREDIT", "REF456", "", "+85,000.00", "1,30,678.90"]
        txn = self.parser._row_to_transaction(row)
        assert txn.debit_credit == "credit"
        assert txn.amount == pytest.approx(85000.00)

    def test_auto_generated_id(self):
        row = ["01 Feb, 2026", "UPI TEST", "REF", "-100.00", "", "500.00"]
        txn = self.parser._row_to_transaction(row)
        assert txn.id is not None
        assert len(txn.id) > 0

    def test_statement_id_is_none(self):
        row = ["01 Feb, 2026", "UPI TEST", "REF", "-100.00", "", "500.00"]
        txn = self.parser._row_to_transaction(row)
        assert txn.statement_id is None

    def test_neither_debit_nor_credit_raises(self):
        row = ["01 Feb, 2026", "UPI TEST", "REF", "", "", "500.00"]
        with pytest.raises(ValueError):
            self.parser._row_to_transaction(row)

    def test_returns_transaction_instance(self):
        row = ["01 Feb, 2026", "UPI TEST", "REF", "-100.00", "", "500.00"]
        assert isinstance(self.parser._row_to_transaction(row), Transaction)

    def test_two_transactions_have_different_ids(self):
        row = ["01 Feb, 2026", "UPI TEST", "REF", "-100.00", "", "500.00"]
        t1 = self.parser._row_to_transaction(row)
        t2 = self.parser._row_to_transaction(row)
        assert t1.id != t2.id


class TestKotakParserAttributes:
    def test_version(self):
        assert KotakParser.version == "1.1.0"

    def test_bank_name(self):
        assert KotakParser.bank_name == "kotak"


# ---------------------------------------------------------------------------
# Registry unit tests — no PDF required
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_get_parser_returns_kotak(self):
        assert isinstance(get_parser("kotak"), KotakParser)

    def test_get_parser_case_insensitive(self):
        assert isinstance(get_parser("KOTAK"), KotakParser)

    def test_get_parser_unknown_raises(self):
        with pytest.raises(KeyError):
            get_parser("nonexistent_bank")

    def test_detect_parser_returns_none_for_unrecognised(self, tmp_path):
        fake_pdf = tmp_path / "fake.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        assert detect_parser(str(fake_pdf)) is None

    def test_detect_parser_mocked_kotak(self):
        with patch.object(KotakParser, "detect", return_value=True):
            result = detect_parser("any_path.pdf")
        assert isinstance(result, KotakParser)


# ---------------------------------------------------------------------------
# Integration tests — require real PDF + KOTAK_PDF_PASSWORD env var
# ---------------------------------------------------------------------------

_SKIP_INTEGRATION = not REAL_PDF.exists() or not _PASSWORD
_SKIP_REASON = "Real Kotak PDF not found or KOTAK_PDF_PASSWORD not set"


@pytest.mark.skipif(_SKIP_INTEGRATION, reason=_SKIP_REASON)
class TestKotakParserIntegration:
    def setup_method(self):
        self.parser = KotakParser()
        self.transactions = self.parser.parse(str(REAL_PDF), password=_PASSWORD)

    def test_returns_list(self):
        assert isinstance(self.transactions, list)

    def test_expected_transaction_count(self):
        assert len(self.transactions) == 81

    def test_all_are_transaction_instances(self):
        for txn in self.transactions:
            assert isinstance(txn, Transaction)

    def test_all_amounts_positive(self):
        for txn in self.transactions:
            assert txn.amount > 0

    def test_all_debit_credit_valid(self):
        for txn in self.transactions:
            assert txn.debit_credit in ("debit", "credit")

    def test_all_dates_in_feb_2026(self):
        for txn in self.transactions:
            assert txn.txn_date.year == 2026
            assert txn.txn_date.month == 2

    def test_no_statement_id_set(self):
        for txn in self.transactions:
            assert txn.statement_id is None

    def test_all_ids_unique(self):
        ids = [txn.id for txn in self.transactions]
        assert len(ids) == len(set(ids))

    def test_no_raw_newlines_in_descriptions(self):
        for txn in self.transactions:
            assert "\n" not in txn.raw_description

    def test_running_balance_present_on_most_rows(self):
        with_balance = [t for t in self.transactions if t.running_balance is not None]
        assert len(with_balance) >= 78

    def test_detect_returns_true(self):
        assert self.parser.detect(str(REAL_PDF), password=_PASSWORD) is True

    def test_detect_parser_finds_kotak(self):
        result = detect_parser(str(REAL_PDF), password=_PASSWORD)
        assert isinstance(result, KotakParser)


class TestSmokeFixture:
    """The release workflow uploads this PDF through `uvx finance-copilot` on
    three OSes; it was once committed empty, which would have failed every
    release. Regenerate with scripts/make_smoke_statement.py."""

    def test_generic_parser_accepts_it_with_a_verified_balance_chain(self):
        from pathlib import Path

        from finance_copilot.parsers.generic import GenericStatementParser

        pdf = str(Path(__file__).parent / "fixtures" / "sample_statement.pdf")
        parser = GenericStatementParser()
        assert parser.detect(pdf)
        txns = parser.parse(pdf)
        assert len(txns) == 8
        assert txns[-1].running_balance == 101755.25


# ---------------------------------------------------------------------------
# Kotak's mid-2026 layout: "#" column, "01 Jul 2026" dates, and no "Kotak"
# text on page one (the name is only in the logo image). Rows mirror the real
# pdfplumber output, with synthetic names and numbers.
# ---------------------------------------------------------------------------

_CURRENT_LAYOUT_PAGE_TEXT = """Account Statement
01 Jul 2026 - 31 Jul 2026
Test User Account No. 1234567890
MICR 400485012 IFSC Code KKBK0000123
Savings Account Transactions
# Date Description Chq/Ref. No. Withdrawal (Dr.) Deposit (Cr.) Balance
7 01 Jul 2026 UPI/TEST USER/HDFC/100000000007/self UPI-200000000007 2,000.00 8,000.00
"""

_CURRENT_LAYOUT_TABLE = [
    ["Savings Account Transactions", None, None, None, None, None, None],
    ["#", "Date", "Description", "Chq/Ref. No.", "Withdrawal (Dr.)", "Deposit (Cr.)", "Balance"],
    ["-", "-", "Opening Balance", "-", "-", "-", "10,000.00"],
    ["1", "01 Jul 2026", "UPI/Urban Company/YESB/100000000001/UPI", "UPI-200000000001", "500.00", "", "9,500.00"],
    ["2", "01 Jul 2026", "UPI/TEST USER/HDFC/100000000002/self", "UPI-200000000002", "1,500.00", "", "8,000.00"],
    ["3", "02 Jul 2026", "NACH-MUT-DR-INDIAN CLEARING CORP-\nP52617097X151772", "NACHDR020626001898\n67", "1,000.00", "", "7,000.00"],
    ["4", "03 Jul 2026", "UPI/EMPLOYER/ICIC/100000000004/Salary", "UPI-200000000004", "", "50,000.00", "57,000.00"],
]


class TestKotakCurrentLayout:
    def test_identified_by_ifsc_not_by_counterparty_bank_names(self):
        from finance_copilot.parsers.bank_identity import identify_bank

        assert identify_bank(_CURRENT_LAYOUT_PAGE_TEXT) == "kotak"

    def test_parses_and_balance_checks_rows(self):
        txns = KotakParser()._parse_current([_CURRENT_LAYOUT_TABLE])

        assert [(t.txn_date, t.amount, t.debit_credit, t.running_balance) for t in txns] == [
            (date(2026, 7, 1), 500.0, "debit", 9500.0),
            (date(2026, 7, 1), 1500.0, "debit", 8000.0),
            (date(2026, 7, 2), 1000.0, "debit", 7000.0),
            (date(2026, 7, 3), 50000.0, "credit", 57000.0),
        ]
        assert txns[2].raw_description == "NACH-MUT-DR-INDIAN CLEARING CORP- P52617097X151772"
        assert txns[0].upi_meta is not None

    def test_legacy_reader_ignores_current_layout(self):
        assert KotakParser()._parse_legacy([_CURRENT_LAYOUT_TABLE]) == []

    def test_unverifiable_layout_fails_loudly(self):
        broken = [row[:] for row in _CURRENT_LAYOUT_TABLE]
        for row in broken[3:]:
            row[6] = "1.00"  # balances that never reconcile
        with pytest.raises(ValueError, match="layout"):
            KotakParser()._parse_current([broken])


class TestBankIdentity:
    def setup_method(self):
        from finance_copilot.parsers.bank_identity import identify_bank

        self.identify = identify_bank

    def test_ifsc_beats_names_in_transactions(self):
        text = "HDFC BANK\nIFSC: KKBK0001234\nDate Description Amount\nUPI/X/HDFC/1/UPI"
        assert self.identify(text) == "kotak"

    def test_name_in_header_used_without_ifsc(self):
        assert self.identify("ICICI Bank Statement\nDate Particulars Amount\n") == "icici"

    def test_names_below_table_heading_are_ignored(self):
        text = "Account Statement\nDate Narration Withdrawal\nUPI/A/HDFC/1/UPI\nNEFT KOTAK MAHINDRA"
        assert self.identify(text) is None

    def test_counterparty_code_without_ifsc_label_is_ignored(self):
        assert self.identify("Statement\nDate Description\nUPI/A/KKBK0001234/UPI") is None

    def test_sbi_needs_a_word_boundary(self):
        assert self.identify("SBINTERNATIONAL LTD\nDate Description") is None
        assert self.identify("SBI\nDate Description") == "sbi"
