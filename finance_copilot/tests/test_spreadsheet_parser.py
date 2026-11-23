"""Spreadsheet statements: reading a CSV, XLSX or XLS into the rows the PDF path reads.

Every file here is built in memory (or is a committed fixture), then read with
parsers/spreadsheet.py. The rows then go through the same row reader and balance
check as a PDF's table (parsers/generic.py), so these tests are about what is
specific to a sheet: what kind of file it is, where its table starts, which
column is which, what the rows above the table say, and the plain-words errors.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from finance_copilot.parsers import spreadsheet as sheet
from finance_copilot.parsers.spreadsheet import SpreadsheetError

FIXTURES = Path(__file__).parent / "fixtures"

# October of the fictional account the PDF fixtures use, as a bank's export would
# print it: (date, narration, withdrawal, deposit, balance), opening balance 25,000.
OCT = [
    ("01/10/2026", "UPI/SWIGGY/ORDER/400112233/UPI", "412.00", "", "24,588.00"),
    ("02/10/2026", "UPI/BMRCL METRO/RECHARGE/400112234", "200.00", "", "24,388.00"),
    ("03/10/2026", "NEFT/ACME LABS PVT LTD/SALARY OCT", "", "85,000.00", "109,388.00"),
    ("04/10/2026", "UPI/ZEPTO/ORDER/400112235/UPI", "643.50", "", "108,744.50"),
]
HEADER = ["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Balance"]


def as_csv(rows, delimiter=",", bom=False, encoding="utf-8") -> bytes:
    def cell(value):
        text = str(value)
        return f'"{text}"' if delimiter in text or '"' in text else text

    text = "\r\n".join(delimiter.join(cell(c) for c in row) for row in rows) + "\r\n"
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode(encoding)


def as_xlsx(rows) -> bytes:
    book = Workbook()
    ws = book.active
    for row in rows:
        ws.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def read(contents: bytes):
    return sheet.read_table(contents)


def parsed(contents: bytes, mapping=None):
    table = read(contents)
    return sheet.parse_table(table, mapping or sheet.starting_mapping(table))


# --- what kind of file it is ------------------------------------------------


class TestKindOfFile:
    def test_a_pdf_is_a_pdf_whatever_it_is_called(self):
        assert sheet.sniff_kind(b"%PDF-1.7 rest") == "pdf"
        assert sheet.sniff_kind(b"junk\n%PDF-1.4") == "pdf"

    def test_a_csv_is_text_with_rows(self):
        assert sheet.sniff_kind(as_csv([HEADER, *OCT])) == "csv"

    def test_an_xlsx_is_a_zip_with_a_workbook_in_it(self):
        assert sheet.sniff_kind(as_xlsx([HEADER])) == "xlsx"

    def test_a_zip_that_is_not_a_workbook_is_not_a_statement(self):
        import zipfile

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("word/document.xml", "<x/>")
        assert sheet.sniff_kind(out.getvalue()) is None

    def test_an_old_xls_is_an_ole_file(self):
        assert sheet.sniff_kind(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600) == "xls"

    def test_an_html_table_saved_as_xls_is_html(self):
        assert sheet.sniff_kind(b"  <html><body><table><tr><td>x</td></tr></table>") == "html"
        assert sheet.sniff_kind(b"<TABLE border=1><tr><td>x</td></tr>") == "html"

    def test_binary_that_is_none_of_these_is_refused(self):
        assert sheet.sniff_kind(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100) is None
        assert sheet.sniff_kind(b"") is None

    def test_a_name_that_lies_does_not_matter(self):
        """Sniffed by content: the same bytes are a CSV under any name."""
        assert sheet.sniff_kind(as_csv([HEADER, *OCT])) == "csv"


# --- reading rows -----------------------------------------------------------


class TestCsv:
    def test_separate_paid_out_and_paid_in_columns(self):
        got = parsed(as_csv([HEADER, *OCT]))
        assert [(t.txn_date, t.amount, t.debit_credit) for t in got.transactions] == [
            (date(2026, 10, 1), 412.0, "debit"),
            (date(2026, 10, 2), 200.0, "debit"),
            (date(2026, 10, 3), 85000.0, "credit"),
            (date(2026, 10, 4), 643.5, "debit"),
        ]
        assert [t.running_balance for t in got.transactions] == [24588.0, 24388.0, 109388.0, 108744.5]
        assert got.transactions[0].raw_description == "UPI/SWIGGY/ORDER/400112233/UPI"
        assert got.has_balance

    def test_one_signed_amount_column(self):
        rows = [["Date", "Narration", "Amount", "Balance"]] + [
            [d, n, f"-{w}" if w else dep, b] for d, n, w, dep, b in OCT
        ]
        got = parsed(as_csv(rows))
        assert [t.debit_credit for t in got.transactions] == ["debit", "debit", "credit", "debit"]
        assert [t.amount for t in got.transactions] == [412.0, 200.0, 85000.0, 643.5]

    def test_an_amount_with_a_debit_or_credit_column(self):
        rows = [["Date", "Narration", "Amount", "Dr/Cr", "Balance"]] + [
            [d, n, w or dep, "Dr" if w else "Cr", b] for d, n, w, dep, b in OCT
        ]
        got = parsed(as_csv(rows))
        assert [t.debit_credit for t in got.transactions] == ["debit", "debit", "credit", "debit"]

    def test_a_semicolon_file(self):
        got = parsed(as_csv([HEADER, *OCT], delimiter=";"))
        assert len(got.transactions) == 4
        assert got.transactions[2].amount == 85000.0

    def test_a_tab_file(self):
        got = parsed(as_csv([HEADER, *OCT], delimiter="\t"))
        assert len(got.transactions) == 4

    def test_a_byte_order_mark(self):
        got = parsed(as_csv([HEADER, *OCT], bom=True))
        assert len(got.transactions) == 4
        assert read(as_csv([HEADER, *OCT], bom=True)).rows[0][0] == "Date"

    def test_windows_1252_text(self):
        rows = [HEADER, ("01/10/2026", "CAFÉ PAYMENT", "10.00", "", "24,990.00"),
                ("02/10/2026", "UPI/X/1", "", "5.00", "24,995.00")]
        got = parsed(as_csv(rows, encoding="cp1252"))
        assert got.transactions[0].raw_description == "CAFÉ PAYMENT"

    def test_utf_16_text(self):
        assert len(parsed(as_csv([HEADER, *OCT], encoding="utf-16")).transactions) == 4

    def test_a_quoted_comma_and_indian_digit_grouping(self):
        rows = [HEADER, ("01/10/2026", "UPI/A, B/1", "1,00,000.50", "", "2,00,000.50"),
                ("02/10/2026", "UPI/C/2", "", "10.00", "2,00,010.50")]
        got = parsed(as_csv(rows))
        assert got.transactions[0].raw_description == "UPI/A, B/1"
        assert got.transactions[0].amount == 100000.5

    def test_the_rows_come_back_oldest_first_whichever_way_the_file_runs(self):
        newest_first = [HEADER, *reversed(OCT)]
        got = parsed(as_csv(newest_first))
        assert [t.txn_date.day for t in got.transactions] == [1, 2, 3, 4]

    def test_dates_in_other_common_shapes(self):
        rows = [HEADER, ("01-Oct-2026", "UPI/A/1", "10.00", "", "24,990.00"),
                ("2026-10-02", "UPI/B/2", "", "5.00", "24,995.00"),
                ("03 Oct 2026", "UPI/C/3", "5.00", "", "24,990.00")]
        got = parsed(as_csv(rows))
        assert [t.txn_date.day for t in got.transactions] == [1, 2, 3]

    def test_a_blank_line_and_a_footer_are_not_payments(self):
        rows = [HEADER, *OCT, ("", "", "", "", ""), ("", "This is a computer generated statement", "", "", "")]
        got = parsed(as_csv(rows))
        assert len(got.transactions) == 4
        assert got.transactions[-1].raw_description == "UPI/ZEPTO/ORDER/400112235/UPI"


class TestXlsx:
    def test_real_date_cells_and_number_cells(self):
        rows = [HEADER] + [
            [datetime(2026, 10, int(d[:2])), n, float(w.replace(",", "")) if w else None,
             float(dep.replace(",", "")) if dep else None, float(b.replace(",", ""))]
            for d, n, w, dep, b in OCT
        ]
        got = parsed(as_xlsx(rows))
        assert [(t.txn_date, t.amount, t.debit_credit) for t in got.transactions][:3] == [
            (date(2026, 10, 1), 412.0, "debit"),
            (date(2026, 10, 2), 200.0, "debit"),
            (date(2026, 10, 3), 85000.0, "credit"),
        ]
        assert got.transactions[3].running_balance == 108744.5

    def test_text_dates_and_text_amounts_in_a_workbook_read_like_a_csv(self):
        got = parsed(as_xlsx([HEADER, *OCT]))
        assert len(got.transactions) == 4

    def test_floating_point_noise_in_a_cell_is_rounded_away(self):
        rows = [HEADER, [datetime(2026, 10, 1), "UPI/A/1", 412.0, None, 24588.000000000004],
                [datetime(2026, 10, 2), "UPI/B/2", None, 5.0, 24593.000000000004]]
        got = parsed(as_xlsx(rows))
        assert got.transactions[0].running_balance == 24588.0

    def test_the_first_sheet_with_a_table_is_read(self):
        book = Workbook()
        book.active.append(["Notes"])
        second = book.create_sheet("Statement")
        for row in [HEADER, *OCT]:
            second.append(row)
        out = io.BytesIO()
        book.save(out)
        assert len(parsed(out.getvalue()).transactions) == 4

    def test_a_file_that_is_not_a_workbook_says_so(self):
        with pytest.raises(SpreadsheetError, match="isn't a statement"):
            read(b"PK\x03\x04 not really")


class TestXls:
    def test_a_real_xls(self):
        got = parsed((FIXTURES / "sample_statement.xls").read_bytes())
        assert len(got.transactions) == 8
        assert got.transactions[0].txn_date == date(2026, 10, 1)
        assert got.transactions[2].amount == 85000.0
        assert got.account_ref == "XXXX0000"

    def test_a_damaged_xls_says_so_in_plain_words(self):
        with pytest.raises(SpreadsheetError, match="couldn't be opened"):
            read(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)


HTML_XLS = """<html><head><meta charset="utf-8"></head><body>
<table><tr><td>Account Number</td><td>50100123456789</td></tr>
<tr><td>Statement Period</td><td>01/10/2026 to 31/10/2026</td></tr></table>
<table border="1">
<tr><th>Date</th><th>Narration</th><th>Withdrawal Amt.</th><th>Deposit Amt.</th><th>Balance</th></tr>
{rows}
</table></body></html>"""


def html_xls(rows=OCT) -> bytes:
    body = "\n".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return HTML_XLS.format(rows=body).encode()


class TestHtmlDisguisedXls:
    def test_the_table_is_read_with_nothing_but_the_standard_library(self):
        got = parsed(html_xls())
        assert len(got.transactions) == 4
        assert got.transactions[2].amount == 85000.0

    def test_the_tables_above_the_payments_are_the_preamble(self):
        got = parsed(html_xls())
        assert got.account_ref == "50100123456789"
        assert got.period == (date(2026, 10, 1), date(2026, 10, 31))

    def test_entities_and_nested_whitespace_are_text(self):
        rows = [("01/10/2026", "UPI/A&amp;B/1", "10.00", "", "24,990.00"),
                ("02/10/2026", "UPI/C/2", "", "5.00", "24,995.00")]
        got = parsed(html_xls(rows))
        assert got.transactions[0].raw_description == "UPI/A&B/1"

    def test_it_is_found_by_content_not_by_name(self):
        assert sheet.sniff_kind(html_xls()) == "html"


# --- where the table starts, and what is above it ---------------------------


class TestPreamble:
    PREAMBLE = [
        ["Example Bank - Statement of Account"],
        ["Account Number", "XXXX0000", "", "IFSC", "HDFC0000123"],
        ["Statement Period", "01/10/2026 to 31/10/2026"],
        [],
    ]

    def test_the_account_number_and_the_period_above_the_table(self):
        got = parsed(as_csv([*self.PREAMBLE, HEADER, *OCT]))
        assert got.account_ref == "XXXX0000"
        assert got.period == (date(2026, 10, 1), date(2026, 10, 31))
        assert got.bank == "hdfc"
        assert len(got.transactions) == 4

    def test_a_workbook_preamble_with_real_date_cells(self):
        rows = [
            ["Account No:", "50100123456789"],
            ["From", datetime(2026, 10, 1), "To", datetime(2026, 10, 31)],
            [],
            HEADER,
            *OCT,
        ]
        got = parsed(as_xlsx(rows))
        assert got.account_ref == "50100123456789"
        assert got.period == (date(2026, 10, 1), date(2026, 10, 31))

    def test_no_preamble_means_no_number_and_no_period(self):
        got = parsed(as_csv([HEADER, *OCT]))
        assert got.account_ref is None
        assert got.period is None
        assert got.bank is None

    def test_a_payment_line_is_not_read_as_a_period(self):
        """Only the rows above the table are the header; a date range inside a
        narration is not the statement's period."""
        rows = [HEADER, ("01/10/2026", "UPI/X/from 01/09/2026 to 30/09/2026", "10.00", "", "24,990.00"),
                ("02/10/2026", "UPI/Y/2", "", "5.00", "24,995.00")]
        assert parsed(as_csv(rows)).period is None

    def test_the_table_is_found_below_a_long_preamble(self):
        rows = [[f"line {i}"] for i in range(30)] + [HEADER, *OCT]
        assert len(parsed(as_csv(rows)).transactions) == 4


# --- no balance column ------------------------------------------------------


class TestNoBalanceColumn:
    def test_a_file_without_a_balance_column_is_accepted(self):
        rows = [HEADER[:4]] + [r[:4] for r in OCT]
        got = parsed(as_csv(rows))
        assert len(got.transactions) == 4
        assert not got.has_balance
        assert all(t.running_balance is None for t in got.transactions)

    def test_a_signed_amount_with_no_balance_needs_no_balance_to_know_the_direction(self):
        rows = [["Date", "Narration", "Amount"]] + [[d, n, f"-{w}" if w else dep] for d, n, w, dep, _ in OCT]
        got = parsed(as_csv(rows))
        assert [t.debit_credit for t in got.transactions] == ["debit", "debit", "credit", "debit"]

    def test_a_file_without_a_balance_keeps_the_order_the_dates_say(self):
        rows = [HEADER[:4]] + [r[:4] for r in reversed(OCT)]
        got = parsed(as_csv(rows))
        assert [t.txn_date.day for t in got.transactions] == [1, 2, 3, 4]


# --- refusals, in plain words -----------------------------------------------


class TestPlainErrors:
    def test_an_empty_file(self):
        with pytest.raises(SpreadsheetError, match="This file is empty"):
            read(b"")
        with pytest.raises(SpreadsheetError, match="This file is empty"):
            read(b"\r\n\r\n,,,\r\n")

    def test_a_balance_that_does_not_add_up_names_the_row(self):
        bad = [list(r) for r in OCT]
        bad[2][4] = "109,000.00"  # the third payment
        with pytest.raises(SpreadsheetError) as caught:
            parsed(as_csv([HEADER, *bad]))
        message = str(caught.value)
        assert "row 4" in message  # the header is row 1, the third payment is row 4
        assert "3 Oct" in message
        assert "109,388.00" in message and "109,000.00" in message

    def test_the_row_number_counts_the_rows_above_the_table(self):
        bad = [list(r) for r in OCT]
        bad[1][4] = "24,000.00"
        rows = [["Account Number", "XXXX0000"], [], HEADER, *bad]
        with pytest.raises(SpreadsheetError) as caught:
            parsed(as_csv(rows))
        assert "row 5" in str(caught.value)  # two rows above, the header, then the second payment

    def test_a_date_that_does_not_read_names_the_row(self):
        rows = [HEADER, OCT[0], ("10/25/2026", "UPI/X/9", "10.00", "", "24,578.00"), OCT[1]]
        with pytest.raises(SpreadsheetError) as caught:
            parsed(as_csv(rows))
        message = str(caught.value)
        assert "10/25/2026" in message and "row 3" in message
        assert "day first" in message

    def test_a_text_in_the_date_column_with_no_amount_is_just_a_note(self):
        rows = [HEADER, *OCT, ("Closing 04/10/2026", "", "", "", "")]
        assert len(parsed(as_csv(rows)).transactions) == 4

    def test_a_file_with_no_table_is_a_question_not_a_crash(self):
        table = read(as_csv([["hello", "world"], ["1", "2"]]))
        assert table.recognised is None
        assert sheet.starting_mapping(table) == {}

    def test_amounts_with_nothing_to_say_which_way_they_went(self):
        rows = [["Date", "Narration", "Amount"]] + [[d, n, w or dep] for d, n, w, dep, _ in OCT]
        with pytest.raises(SpreadsheetError, match="money in or money out"):
            parsed(as_csv(rows))

    def test_no_payments_in_a_table(self):
        with pytest.raises(SpreadsheetError, match="No payments"):
            parsed(as_csv([HEADER, ("", "", "", "", "")]))

    def test_the_messages_use_no_parser_words(self):
        for contents in (b"", as_csv([HEADER, ("10/25/2026", "x", "1.00", "", "")])):
            with pytest.raises(SpreadsheetError) as caught:
                parsed(contents)
            for word in ("Traceback", "ValueError", "csv.Error", "column index", "None"):
                assert word not in str(caught.value)


# --- the columns ------------------------------------------------------------


class TestHeaderAndMapping:
    def test_a_recognised_header_maps_the_way_the_pdf_path_does(self):
        table = read(as_csv([HEADER, *OCT]))
        assert table.recognised is not None
        assert sheet.starting_mapping(table) == {
            "date": 0, "description": 1, "debit": 2, "credit": 3, "balance": 4,
        }
        assert table.columns == HEADER

    def test_an_unrecognised_header_is_guessed_from_what_the_columns_hold(self):
        rows = [["When", "What", "Moved", "Left"]] + [
            [d, n, f"-{w}" if w else dep, b] for d, n, w, dep, b in OCT
        ]
        table = read(as_csv(rows))
        assert table.recognised is None
        assert sheet.starting_mapping(table) == {"date": 0, "description": 1, "amount": 2, "balance": 3}
        assert table.columns == ["When", "What", "Moved", "Left"]
        assert table.first_data == 1

    def test_a_table_with_no_header_row_at_all_has_lettered_columns(self):
        rows = [[d, n, f"-{w}" if w else dep, b] for d, n, w, dep, b in OCT]
        table = read(as_csv(rows))
        assert table.header_row is None
        assert table.first_data == 0
        assert table.columns == ["Column A", "Column B", "Column C", "Column D"]
        assert sheet.starting_mapping(table)["date"] == 0

    def test_the_person_can_choose_the_columns(self):
        rows = [["When", "What", "Out", "In", "Left"]] + [list(r) for r in OCT]
        table = read(as_csv(rows))
        got = sheet.parse_table(table, {"date": 0, "description": 1, "debit": 2, "credit": 3, "balance": 4})
        assert len(got.transactions) == 4

    def test_a_mapping_without_a_date_or_amount_is_refused_in_plain_words(self):
        table = read(as_csv([HEADER, *OCT]))
        with pytest.raises(SpreadsheetError, match="Date"):
            sheet.parse_table(table, {"description": 1, "debit": 2})
        with pytest.raises(SpreadsheetError, match="Paid out, Paid in or Amount"):
            sheet.parse_table(table, {"date": 0, "description": 1})

    def test_one_column_cannot_be_two_things(self):
        table = read(as_csv([HEADER, *OCT]))
        with pytest.raises(SpreadsheetError, match="more than one"):
            sheet.parse_table(table, {"date": 0, "description": 0, "debit": 2})

    def test_a_column_that_is_not_in_the_file(self):
        table = read(as_csv([HEADER, *OCT]))
        with pytest.raises(SpreadsheetError, match="isn't in the file"):
            sheet.parse_table(table, {"date": 0, "description": 1, "debit": 9})

    def test_a_wrong_choice_is_caught_by_the_balance(self):
        """The balance column chosen as the amount: the chain does not add up."""
        table = read(as_csv([HEADER, *OCT]))
        with pytest.raises(SpreadsheetError, match="running balance"):
            sheet.parse_table(table, {"date": 0, "description": 1, "debit": 4, "credit": 3, "balance": 2})


class TestSignature:
    def test_the_same_header_is_the_same_layout(self):
        a = read(as_csv([HEADER, *OCT]))
        b = read(as_csv([HEADER, *OCT[:2]]))
        assert a.signature == b.signature

    def test_case_spacing_and_trailing_blanks_do_not_matter(self):
        a = read(as_csv([HEADER, *OCT]))
        b = read(as_csv([[h.upper() + "  " for h in HEADER] + ["", ""], *OCT]))
        assert a.signature == b.signature

    def test_a_different_header_is_a_different_layout(self):
        a = read(as_csv([HEADER, *OCT]))
        b = read(as_csv([["Date", "Particulars", "Debit", "Credit", "Balance"], *OCT]))
        assert a.signature != b.signature

    def test_the_rows_above_the_table_are_not_part_of_it(self):
        a = read(as_csv([HEADER, *OCT]))
        b = read(as_csv([["Account Number", "XXXX1111"], HEADER, *OCT]))
        assert a.signature == b.signature

    def test_a_file_with_no_header_is_told_by_its_width(self):
        a = read(as_csv([[d, n, w or dep, b] for d, n, w, dep, b in OCT]))
        b = read(as_csv([[d, n, w or dep] for d, n, w, dep, b in OCT]))
        assert a.signature != b.signature


class TestPreview:
    def test_the_first_rows_as_they_would_be_read(self):
        table = read(as_csv([HEADER, *OCT]))
        rows = sheet.preview(table, sheet.starting_mapping(table), limit=3)
        assert rows == {
            "rows": [
                {"date": "2026-10-01", "description": "UPI/SWIGGY/ORDER/400112233/UPI", "amount": 412.0,
                 "direction": "debit", "balance": 24588.0},
                {"date": "2026-10-02", "description": "UPI/BMRCL METRO/RECHARGE/400112234", "amount": 200.0,
                 "direction": "debit", "balance": 24388.0},
                {"date": "2026-10-03", "description": "NEFT/ACME LABS PVT LTD/SALARY OCT", "amount": 85000.0,
                 "direction": "credit", "balance": 109388.0},
            ],
            "error": None,
        }

    def test_a_wrong_choice_comes_back_as_the_message_the_form_shows(self):
        table = read(as_csv([HEADER, *OCT]))
        got = sheet.preview(table, {"date": 1, "description": 1, "debit": 2})
        assert got["rows"] == []
        assert got["error"]

    def test_a_date_read_the_wrong_way_round_shows_before_saving(self):
        """A column of month-first dates reads as nothing, not as the wrong day."""
        rows = [["Date", "Narration", "Amount"], ("10/25/2026", "x", "-1.00"), ("10/26/2026", "y", "2.00")]
        table = read(as_csv(rows))
        assert sheet.preview(table, sheet.starting_mapping(table))["error"]
