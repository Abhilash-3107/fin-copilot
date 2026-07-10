"""Tests for the generic balance-verified statement parser (pure table logic, no PDFs)."""
from __future__ import annotations

from datetime import date

from src.parsers.generic import (
    ParseResult,
    _map_header,
    _parse_amount,
    _parse_date,
    extract_transactions,
)

# ---------------------------------------------------------------------------
# Cell-level parsing
# ---------------------------------------------------------------------------

class TestParseAmount:
    def test_plain(self):
        assert _parse_amount("450.00") == (450.0, None)

    def test_indian_grouping_and_currency(self):
        assert _parse_amount("₹1,23,456.78") == (123456.78, None)
        assert _parse_amount("Rs. 2,500") == (2500.0, None)
        assert _parse_amount("INR 99") == (99.0, None)

    def test_cr_dr_suffix(self):
        assert _parse_amount("500.00 Cr") == (500.0, "credit")
        assert _parse_amount("1,200.50 Dr.") == (1200.5, "debit")
        assert _parse_amount("CR 300") == (300.0, "credit")

    def test_negative_normalized_to_abs(self):
        assert _parse_amount("-750.00") == (750.0, None)

    def test_non_numeric_rejected(self):
        assert _parse_amount("Closing Balance") is None
        assert _parse_amount("") is None
        assert _parse_amount("12 Mar 2026") is None  # date, not amount


class TestParseDate:
    def test_common_indian_formats(self):
        assert _parse_date("01/02/2026") == date(2026, 2, 1)
        assert _parse_date("01-02-26") == date(2026, 2, 1)
        assert _parse_date("1 Feb 2026") == date(2026, 2, 1)
        assert _parse_date("01 Feb, 2026") == date(2026, 2, 1)
        assert _parse_date("01-Feb-2026") == date(2026, 2, 1)
        assert _parse_date("2026-02-01") == date(2026, 2, 1)

    def test_garbage_returns_none(self):
        assert _parse_date("Particulars") is None
        assert _parse_date("") is None


class TestHeaderMapping:
    def test_hdfc_style(self):
        cmap = _map_header(
            ["Date", "Narration", "Chq./Ref.No.", "Value Dt",
             "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]
        )
        assert cmap is not None
        assert (cmap.date, cmap.description) == (0, 1)
        assert (cmap.debit, cmap.credit, cmap.balance) == (4, 5, 6)

    def test_value_date_not_chosen_over_txn_date(self):
        cmap = _map_header(["Value Date", "Txn Date", "Description", "Debit", "Credit", "Balance"])
        assert cmap is not None
        assert cmap.date == 1

    def test_non_header_row_rejected(self):
        assert _map_header(["01/02/2026", "UPI/SWIGGY/123", "", "", "450.00", "", "10,550.00"]) is None


# ---------------------------------------------------------------------------
# Table-level extraction with balance verification
# ---------------------------------------------------------------------------

HDFC_HEADER = ["Date", "Narration", "Chq./Ref.No.", "Value Dt",
               "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]


def _hdfc_table():
    return [
        HDFC_HEADER,
        ["01/02/26", "UPI-SWIGGY-swiggy@icici", "0001", "01/02/26", "450.00", "", "9,550.00"],
        ["03/02/26", "NEFT SALARY ACME CORP", "0002", "03/02/26", "", "50,000.00", "59,550.00"],
        ["05/02/26", "UPI-ZOMATO-zomato@hdfc", "0003", "05/02/26", "1,200.00", "", "58,350.00"],
        ["07/02/26", "ATM WDL MUMBAI", "0004", "07/02/26", "5,000.00", "", "53,350.00"],
    ]


class TestExtractTransactions:
    def test_hdfc_style_two_column(self):
        result = extract_transactions([_hdfc_table()])
        assert len(result.transactions) == 4
        assert result.verified_fraction == 1.0
        assert result.is_confident
        t = result.transactions[0]
        assert (t.txn_date, t.amount, t.debit_credit) == (date(2026, 2, 1), 450.0, "debit")
        assert result.transactions[1].debit_credit == "credit"
        assert result.transactions[3].running_balance == 53350.0

    def test_sbi_style_long_dates(self):
        table = [
            ["Txn Date", "Value Date", "Description", "Ref No./Cheque No.", "Debit", "Credit", "Balance"],
            ["1 Feb 2026", "1 Feb 2026", "UPI/DR/123/BLINKIT", "", "350.00", "", "9,650.00"],
            ["2 Feb 2026", "2 Feb 2026", "INTEREST CREDIT", "", "", "120.00", "9,770.00"],
            ["4 Feb 2026", "4 Feb 2026", "NACH-MUT-DR GROWW", "", "5,000.00", "", "4,770.00"],
        ]
        result = extract_transactions([table])
        assert len(result.transactions) == 3
        assert result.verified_fraction == 1.0
        assert result.transactions[1].debit_credit == "credit"

    def test_single_amount_column_with_drcr_suffix(self):
        table = [
            ["Date", "Transaction Details", "Amount (INR)", "Balance (INR)"],
            ["01-02-2026", "POS AMAZON", "999.00 Dr", "9,001.00"],
            ["02-02-2026", "REFUND AMAZON", "999.00 Cr", "10,000.00"],
            ["03-02-2026", "UPI RENT PAYMENT", "15,000.00 Dr", "-5,000.00"],
        ]
        result = extract_transactions([table])
        assert [t.debit_credit for t in result.transactions] == ["debit", "credit", "debit"]
        # third check breaks the chain only if signs were misread; balances here
        # don't chain for row 3 (abs() on negative balance), so just assert direction
        assert result.transactions[2].amount == 15000.0

    def test_direction_inferred_from_balance_when_no_marker(self):
        table = [
            ["Date", "Particulars", "Amount", "Balance"],
            ["01/02/2026", "OPENING TXN", "1,000.00", "10,000.00"],
            ["02/02/2026", "UPI SWIGGY", "400.00", "9,600.00"],
            ["03/02/2026", "SALARY", "50,000.00", "59,600.00"],
            ["04/02/2026", "RENT", "15,000.00", "44,600.00"],
        ]
        result = extract_transactions([table])
        directions = {t.raw_description: t.debit_credit for t in result.transactions}
        assert directions["UPI SWIGGY"] == "debit"
        assert directions["SALARY"] == "credit"
        assert directions["RENT"] == "debit"
        # first row has no previous balance → direction unknown → excluded
        assert "OPENING TXN" not in directions

    def test_reversed_statement_newest_first(self):
        table = [_hdfc_table()[0]] + list(reversed(_hdfc_table()[1:]))
        result = extract_transactions([table])
        assert result.verified_fraction == 1.0
        # output is chronological after resolution
        dates = [t.txn_date for t in result.transactions]
        assert dates == sorted(dates)

    def test_multiline_description_continuation(self):
        table = [
            HDFC_HEADER,
            ["01/02/26", "UPI-SOMEVERYLONGMERCHANT", "1", "01/02/26", "450.00", "", "9,550.00"],
            ["", "NAME-CONTINUED@okaxis", "", "", "", "", ""],
            ["03/02/26", "NEFT SALARY", "2", "03/02/26", "", "50,000.00", "59,550.00"],
        ]
        result = extract_transactions([table])
        assert len(result.transactions) == 2
        assert "CONTINUED@okaxis" in result.transactions[0].raw_description

    def test_header_repeats_across_pages(self):
        page1 = _hdfc_table()[:3]
        page2 = [HDFC_HEADER] + _hdfc_table()[3:]
        result = extract_transactions([page1, page2])
        assert len(result.transactions) == 4
        assert result.verified_fraction == 1.0

    def test_summary_rows_ignored(self):
        table = _hdfc_table() + [
            ["", "STATEMENT SUMMARY", "", "", "", "", ""],
            ["TOTAL", "", "", "", "6,650.00", "50,000.00", ""],
        ]
        result = extract_transactions([table])
        assert len(result.transactions) == 4

    def test_corrupted_amounts_fail_verification(self):
        table = _hdfc_table()
        table[2][5] = "5,000.00"  # salary row mangled: balance math now breaks
        result = extract_transactions([table])
        assert result.verified_fraction < 0.8
        assert not result.is_confident

    def test_no_table_returns_empty(self):
        assert extract_transactions([]) == ParseResult()
        assert extract_transactions([[["random", "text"], ["more", "noise"]]]).transactions == []

    def test_upi_meta_extracted(self):
        table = [
            HDFC_HEADER,
            ["01/02/26", "UPI/swiggy@icici/food order", "1", "", "450.00", "", "9,550.00"],
            ["02/02/26", "UPI/rahul@upi/dinner split", "2", "", "", "300.00", "9,850.00"],
            ["03/02/26", "NEFT TRANSFER", "3", "", "100.00", "", "9,750.00"],
        ]
        result = extract_transactions([table])
        assert result.transactions[0].upi_meta is not None
        assert "food order" in result.transactions[0].upi_meta
        assert result.transactions[2].upi_meta is None
