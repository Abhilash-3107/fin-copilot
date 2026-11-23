"""A spreadsheet is somebody else's file: it must not be able to take the machine.

Each limit is tested by lowering it, so the files stay small; what matters is that
the refusal is in plain words, that nothing is read past the limit, and that a
hostile workbook (an XML bomb, a zip bomb, an unreadable zip) is a refusal and
never a crash.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from finance_copilot.parsers import spreadsheet as sheet
from finance_copilot.parsers.spreadsheet import SpreadsheetError

FIXTURES = Path(__file__).parent / "fixtures"
HEAD = "Date,Narration,Withdrawal Amt.,Deposit Amt.,Balance\n"


def csv_of(rows: int, width: int = 5) -> bytes:
    cells = ["01/10/2026", "UPI/X/1", "1.00", "", ""] + [""] * max(0, width - 6) + ["end"] * (width > 5)
    return (HEAD + (",".join(cells[:width]) + "\n") * rows).encode()


def xlsx_with(parts: dict[str, bytes]) -> bytes:
    """The sample workbook with some parts replaced or added."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO((FIXTURES / "sample_statement.xlsx").read_bytes())) as source, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in source.infolist():
            z.writestr(info.filename, parts.pop(info.filename, source.read(info.filename)))
        for name, body in parts.items():
            z.writestr(name, body)
    return out.getvalue()


class TestRows:
    def test_a_csv_over_the_row_limit_is_refused_and_not_read_past_it(self, monkeypatch):
        monkeypatch.setattr(sheet, "MAX_ROWS", 100)
        with pytest.raises(SpreadsheetError, match="more than 100 rows"):
            sheet.read_table(csv_of(101))
        assert len(sheet.read_table(csv_of(99)).rows) == 100  # the header and 99 payments

    def test_the_limit_is_fifty_thousand(self):
        assert sheet.MAX_ROWS == 50_000 and sheet.MAX_COLUMNS == 64

    def test_a_workbook_over_the_row_limit(self, monkeypatch):
        from openpyxl import Workbook

        book = Workbook()
        for _ in range(30):
            book.active.append(["01/10/2026", "x", 1])
        out = io.BytesIO()
        book.save(out)
        monkeypatch.setattr(sheet, "MAX_ROWS", 20)
        with pytest.raises(SpreadsheetError, match="more than 20 rows"):
            sheet.read_table(out.getvalue())

    def test_an_html_table_over_the_row_limit(self, monkeypatch):
        monkeypatch.setattr(sheet, "MAX_ROWS", 20)
        html = b"<table>" + b"<tr><td>01/10/2026<td>x<td>1" * 30 + b"</table>"
        with pytest.raises(SpreadsheetError, match="more than 20 rows"):
            sheet.read_table(html)


class TestColumns:
    def test_a_csv_with_too_many_columns(self):
        with pytest.raises(SpreadsheetError, match="more than 64 columns"):
            sheet.read_table(csv_of(3, width=65))
        assert sheet.read_table(csv_of(3, width=64)).width == 64

    def test_blank_cells_at_the_end_of_a_row_do_not_count(self):
        sheet.read_table((HEAD + "01/10/2026,x,1.00,,," + "," * 200 + "\n").encode())

    def test_a_workbook_with_too_many_columns(self):
        from openpyxl import Workbook

        book = Workbook()
        book.active.append(list(range(70)))
        out = io.BytesIO()
        book.save(out)
        with pytest.raises(SpreadsheetError, match="more than 64 columns"):
            sheet.read_table(out.getvalue())

    def test_an_html_page_of_endless_markup(self, monkeypatch):
        monkeypatch.setattr(sheet, "MAX_MARKUP_TAGS", 100)
        with pytest.raises(SpreadsheetError, match="more markup"):
            sheet.read_table(b"<table>" + b"<div>" * 200 + b"</table>")

    def test_an_html_table_with_a_huge_colspan(self):
        html = b"<table>" + b"<tr><td colspan=50>1<td colspan=50>2<td>3" * 3 + b"</table>"
        with pytest.raises(SpreadsheetError, match="more than 64 columns"):
            sheet.read_table(html)


class TestHeaderSearch:
    def test_a_table_further_down_than_the_sample_is_not_looked_for(self):
        rows = "".join(f"line {i}\n" for i in range(80)) + HEAD + "01/10/2026,x,1.00,,\n"
        table = sheet.read_table(rows.encode())
        assert table.header_row is None and not sheet.has_payment_rows(table)

    def test_a_table_inside_the_sample_is_found(self):
        rows = "".join(f"line {i}\n" for i in range(40)) + HEAD + "01/10/2026,x,1.00,,\n"
        assert sheet.read_table(rows.encode()).header_row == 40


class TestWorkbooksThatAreBombs:
    def test_a_workbook_that_would_unpack_to_too_much(self, monkeypatch):
        monkeypatch.setattr(sheet, "MAX_UNCOMPRESSED_BYTES", 10_000)
        data = xlsx_with({"xl/extra.bin": b"x" * 1000 + bytes(range(256)) * 40})
        with pytest.raises(SpreadsheetError, match="too large|too much"):
            sheet.read_table(data)

    def test_a_member_that_unpacks_to_a_hundred_times_its_size(self):
        data = xlsx_with({"xl/extra.bin": b"\0" * 3_000_000})
        with pytest.raises(SpreadsheetError, match="too large|too much"):
            sheet.read_table(data)

    def test_the_sample_workbook_is_within_the_limits(self):
        assert sheet.read_table((FIXTURES / "sample_statement.xlsx").read_bytes()).recognised is not None

    LOLZ = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        + b"".join(
            b'<!ENTITY lol%d "%s">' % (i, (b"&lol%d;" % (i - 1) if i > 1 else b"&lol;") * 10)
            for i in range(1, 10)
        )
        + b']><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&lol9;</t></is></c></row></sheetData></worksheet>'
    )

    def test_an_xml_entity_bomb_in_a_workbook_is_refused_quickly(self):
        import time

        data = xlsx_with({"xl/worksheets/sheet1.xml": self.LOLZ})
        started = time.time()
        with pytest.raises(SpreadsheetError, match="couldn't be"):
            sheet.read_table(data)
        assert time.time() - started < 5

    def test_the_xml_library_that_refuses_entities_is_the_one_in_use(self):
        import defusedxml  # noqa: F401
        from openpyxl.xml import DEFUSEDXML

        assert DEFUSEDXML


class TestDamagedFiles:
    def test_a_zip_whose_method_this_python_cannot_read(self):
        data = bytearray((FIXTURES / "sample_statement.xlsx").read_bytes())
        for signature, at in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
            i = data.find(signature)
            while i != -1:
                data[i + at] = 99
                i = data.find(signature, i + 4)
        with pytest.raises(SpreadsheetError, match="couldn't be opened|couldn't be read"):
            sheet.read_table(bytes(data))

    def test_a_zip_that_asks_for_a_newer_zip_than_this_python_reads(self):
        data = bytearray((FIXTURES / "sample_statement.xlsx").read_bytes())
        for signature, at in ((b"PK\x03\x04", 4), (b"PK\x01\x02", 6)):
            i = data.find(signature)
            while i != -1:
                data[i + at] = 128
                i = data.find(signature, i + 4)
        with pytest.raises(SpreadsheetError, match="couldn't be opened"):
            sheet.read_table(bytes(data))

    def test_a_zip_with_its_directory_cut_off(self):
        data = (FIXTURES / "sample_statement.xlsx").read_bytes()
        with pytest.raises(SpreadsheetError, match="couldn't be opened"):
            sheet.read_table(data[: len(data) // 2])

    def test_a_flipped_byte_anywhere_is_a_refusal_or_a_reading_never_a_crash(self):
        import random

        data = (FIXTURES / "sample_statement.xlsx").read_bytes()
        rng = random.Random(3)
        for _ in range(150):
            broken = bytearray(data)
            for _ in range(rng.randint(1, 8)):
                broken[rng.randrange(len(broken))] = rng.randrange(256)
            try:
                sheet.read_table(bytes(broken))
            except SpreadsheetError:
                pass
