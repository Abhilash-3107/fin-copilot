"""Statements as spreadsheets: CSV, XLSX, XLS, and the HTML tables banks save as .xls.

A spreadsheet is a table, and a table of payments is what the generic PDF parser
already turns into verified rows (parsers/generic.py). This module only gets a
sheet to that point: it says what kind of file it is (by content, never by name),
reads it into rows of text, finds where the table starts, says which column is
which (from the header, or by looking at what the columns hold, or as the person
chose), and reads the account number and period from the rows above the table.
The rows then go through generic.extract_with_map, the one row reader, and the
balance check of the PDF path.

Everything here works on bytes and lists of strings, so it is tested without
files on disk. Every refusal is a SpreadsheetError whose message is for the person
who dropped the file: no parser words, and the row where it can name one.
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from html.parser import HTMLParser

from finance_copilot.models.transaction import Transaction
from finance_copilot.money import rupees
from finance_copilot.parsers import generic
from finance_copilot.parsers.bank_identity import identify_bank
from finance_copilot.parsers.base import account_ref_in_text
from finance_copilot.parsers.generic import ColumnMap, header_roles, is_negative
from finance_copilot.parsers.period import period_in_header, period_texts

# A real statement is a few thousand rows and a dozen columns. These stop a file
# built to use the machine up: rows and columns are counted while reading, so the
# file is refused when it crosses the line, not after it is all in memory.
MAX_ROWS = 50_000
MAX_COLUMNS = 64
# An HTML page's tags that are not table structure (fonts, spans, divs). A bank's
# page has a few per cell; a million is a page built to keep the parser busy.
MAX_MARKUP_TAGS = 1_000_000

# A workbook is a zip: what it unpacks to is added up from the zip's own directory
# before anything is unpacked. Over the total, or any big member that unpacks to
# more than 100 times its size, is a zip bomb.
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
_MAX_RATIO = 100
_RATIO_FROM_BYTES = 1024 * 1024  # a small member cannot hurt, and a small XML part may compress well

# Banks put their title and the account's details above the table; a table
# further down than this is something else. The header, and the first payment of
# a table with none, are looked for in these rows only.
_SAMPLE_ROWS = 50

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# The roles a column can have, in the order the form lists them, with the words
# it shows. "Ignore" is a column left out of the mapping.
ROLES = ("date", "description", "debit", "credit", "amount", "drcr", "balance")
ROLE_WORDS = {
    "date": "Date",
    "description": "Description",
    "debit": "Paid out",
    "credit": "Paid in",
    "amount": "Amount",
    "drcr": "Debit or credit marker",
    "balance": "Balance",
}


class SpreadsheetError(ValueError):
    """A file that cannot be read as a statement, in words for the person."""


class DateOrderUnclear(SpreadsheetError):
    """The dates could be day first or month first and nothing in the file says
    which: the person is asked (the form's "Dates are day first / month first")."""

    def __init__(self) -> None:
        super().__init__(
            "The dates in this file could be day first (01/10 is 1 October) or month first "
            "(01/10 is 10 January). Choose which way round they are."
        )

DATE_ORDERS = ("dmy", "mdy")


# --- what kind of file it is ------------------------------------------------


def sniff_kind(contents: bytes) -> str | None:
    """'pdf', 'xlsx', 'xls', 'html' (a table saved as .xls) or 'csv' by what the
    bytes are, or None for anything else. A name can lie; the bytes cannot."""
    if not contents.strip():
        return None
    if b"%PDF" in contents[:1024]:
        return "pdf"
    if contents[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(contents)) as z:
                return "xlsx" if "xl/workbook.xml" in z.namelist() else None
        except Exception:
            # A zip that cannot be opened at all (cut off, a newer zip than this
            # Python reads, a damaged directory) is most likely a damaged
            # workbook: the reader says so in words.
            return "xlsx"
    if contents[:8] == _OLE_MAGIC:
        return "xls"
    sample = contents[:4096]
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "csv"  # Excel's "Unicode text"
    if b"\x00" in sample:
        return None
    lowered = sample.lower()
    if b"<html" in lowered or b"<table" in lowered:
        return "html"
    return "csv"


# --- reading rows -----------------------------------------------------------


def _decode(contents: bytes) -> str:
    if contents.startswith((b"\xff\xfe", b"\xfe\xff")):
        return contents.decode("utf-16", errors="replace")
    try:
        return contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        return contents.decode("cp1252", errors="replace")


def _best_delimiter(text: str) -> str:
    """The delimiter that makes the most rows the same width, a comma on a tie.
    A preamble's short lines do not decide it: only rows with two or more cells count."""
    best, chosen = 0, ","
    for delimiter in (",", ";", "\t", "|"):
        try:
            rows = list(csv.reader(io.StringIO(text[:200_000]), delimiter=delimiter))
        except csv.Error:
            continue
        widths = Counter(len(r) for r in rows if len(r) >= 2)
        if widths:
            width, count = widths.most_common(1)[0]
            if count * width > best:
                best, chosen = count * width, delimiter
    return chosen


def _too_many_rows() -> SpreadsheetError:
    return SpreadsheetError(
        f"This file has more than {MAX_ROWS:,} rows, which is more than one statement. "
        "Export a shorter range of dates."
    )


def _too_many_columns() -> SpreadsheetError:
    return SpreadsheetError(
        f"This file has more than {MAX_COLUMNS} columns, which is more than a statement needs. "
        "Check that it is the statement's table."
    )


def _checked_row(cells: list[str]) -> list[str]:
    """A row without its empty cells at the end, refused when it is still too wide."""
    while cells and not cells[-1]:
        cells.pop()
    if len(cells) > MAX_COLUMNS:
        raise _too_many_columns()
    return cells


def _csv_rows(contents: bytes) -> list[list[str]]:
    text = _decode(contents)
    rows: list[list[str]] = []
    try:
        for row in csv.reader(io.StringIO(text), delimiter=_best_delimiter(text)):
            rows.append(_checked_row([cell.strip() for cell in row]))
            if len(rows) > MAX_ROWS:
                raise _too_many_rows()
    except csv.Error as exc:
        raise SpreadsheetError(
            "This file couldn't be read as a table of payments. Export it again as CSV."
        ) from exc
    return rows


def _number_text(value: float) -> str:
    """A number cell as the text a bank would print: no exponent, no float noise."""
    rounded = round(value, 6)
    text = format(Decimal(repr(rounded)), "f")
    return text[:-2] if text.endswith(".0") else text


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _number_text(value)
    return str(value).strip()


def _refuse_a_zip_bomb(contents: bytes) -> None:
    """Add up what the workbook would unpack to, from the zip's directory, before
    openpyxl unpacks any of it."""
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as z:
            members = z.infolist()
    except Exception as exc:
        raise SpreadsheetError(
            "This workbook couldn't be opened. It may be damaged or password-protected."
        ) from exc
    too_big = SpreadsheetError(
        "This workbook is too large to read: it unpacks to far more than a statement needs. "
        "Export the statement again, as CSV if you can."
    )
    if sum(m.file_size for m in members) > MAX_UNCOMPRESSED_BYTES:
        raise too_big
    for m in members:
        if m.file_size >= _RATIO_FROM_BYTES and m.file_size > _MAX_RATIO * max(m.compress_size, 1):
            raise too_big


def _xlsx_sheets(contents: bytes) -> list[list[list[str]]]:
    from openpyxl import load_workbook

    _refuse_a_zip_bomb(contents)
    try:
        book = load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
    except Exception as exc:
        raise SpreadsheetError(
            "This workbook couldn't be opened. It may be damaged or password-protected."
        ) from exc
    sheets: list[list[list[str]]] = []
    try:
        for ws in book.worksheets:
            # Some banks write a wrong size into the sheet, which makes a
            # read-only sheet stop early.
            ws.reset_dimensions()
            rows: list[list[str]] = []
            for values in ws.iter_rows(values_only=True):
                cells = list(values)
                while cells and cells[-1] in (None, ""):
                    cells.pop()
                if len(cells) > MAX_COLUMNS:
                    raise _too_many_columns()
                rows.append(_checked_row([_cell_text(v) for v in cells]))
                if len(rows) > MAX_ROWS:
                    raise _too_many_rows()
            sheets.append(rows)
    except SpreadsheetError:
        raise
    except Exception as exc:
        raise SpreadsheetError(
            "This workbook couldn't be read. It may be damaged or not a table of payments."
        ) from exc
    finally:
        book.close()
    return sheets


def _xls_sheets(contents: bytes) -> list[list[list[str]]]:
    import xlrd

    try:
        # The library writes what it notices about the file (an OLE oddity) to this
        # stream, which defaults to stdout: it goes nowhere.
        book = xlrd.open_workbook(file_contents=contents, on_demand=True, logfile=io.StringIO())
    except Exception as exc:
        raise SpreadsheetError(
            "This workbook couldn't be opened. It may be damaged or password-protected."
        ) from exc
    sheets: list[list[list[str]]] = []
    try:
        for index in range(book.nsheets):
            sheet = book.sheet_by_index(index)
            if sheet.nrows > MAX_ROWS:
                raise _too_many_rows()
            if sheet.ncols > MAX_COLUMNS:
                raise _too_many_columns()
            rows: list[list[str]] = []
            for r in range(sheet.nrows):
                row = []
                for cell in sheet.row(r):
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            row.append(xlrd.xldate_as_datetime(cell.value, book.datemode).date().isoformat())
                        except Exception:
                            row.append(str(cell.value))
                    elif cell.ctype == xlrd.XL_CELL_NUMBER:
                        row.append(_number_text(cell.value))
                    elif cell.ctype in (xlrd.XL_CELL_ERROR, xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        row.append("")
                    else:
                        row.append(_cell_text(cell.value))
                rows.append(_checked_row(row))
            sheets.append(rows)
    except SpreadsheetError:
        raise
    except Exception as exc:
        raise SpreadsheetError(
            "This workbook couldn't be read. It may be damaged or not a table of payments."
        ) from exc
    finally:
        book.release_resources()
    return sheets


class _HtmlTables(HTMLParser):
    """The rows of every table in an HTML page, in page order, as lists of cell text.
    Many netbanking "XLS" downloads are exactly this.

    Each top-level table is built as a grid: a cell with a colspan takes that many
    columns (the extra ones empty), and a cell with a rowspan carries its text down
    into the rows below, so a header written over two rows (a "Date" cell spanning
    both, and "Amount" over "Withdrawal" and "Deposit") lines up. A table inside a
    cell is part of that cell: its text joins the cell's and its rows are not rows
    of the page. Text goes to the innermost open cell; a row or cell left unclosed
    (sloppy HTML) ends where the next begins. Rows and columns are counted as they
    come, so a page built to be enormous is refused without being read to the end."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._depth = 0  # table nesting; only the outermost is a table of the page
        self._row: list[str] | None = None
        self._row_in_table = 0
        self._carried: dict[tuple[int, int], str] = {}  # (row, column) -> text from a rowspan above
        self._cell: list[str] | None = None
        self._colspan = 1
        self._rowspan = 1
        self._markup = 0

    def _carry_into_row(self) -> None:
        """Cells the rows above reach down into, where the row has reached them."""
        while self._row is not None and (self._row_in_table, len(self._row)) in self._carried:
            self._row.append(self._carried.pop((self._row_in_table, len(self._row))))

    def _end_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._carry_into_row()
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            at = len(self._row)
            self._row.append(text)
            self._row.extend([""] * (self._colspan - 1))
            for below in range(1, self._rowspan):
                for across in range(self._colspan):
                    self._carried[(self._row_in_table + below, at + across)] = text if across == 0 else ""
        self._cell = None
        self._colspan = self._rowspan = 1

    def _end_row(self) -> None:
        self._end_cell()
        if self._row is not None:
            self._carry_into_row()
            self.rows.append(_checked_row(self._row))
            if len(self.rows) > MAX_ROWS:
                raise _too_many_rows()
            self._row_in_table += 1
        self._row = None

    def handle_starttag(self, tag, attrs):
        if tag not in ("table", "tr", "td", "th"):
            self._markup += 1
            if self._markup > MAX_MARKUP_TAGS:
                raise SpreadsheetError(
                    "This page has far more markup than a statement needs. "
                    "Export the statement again, as CSV if you can."
                )
        if tag == "table":
            if self._depth == 0:
                self._end_row()
                self._row_in_table = 0
                self._carried = {}
            self._depth += 1
        elif self._depth != 1:
            if tag == "br" and self._cell is not None:
                self._cell.append(" ")  # a table inside a cell is that cell's text
        elif tag == "tr":
            self._end_row()
            self._row = []
        elif tag in ("td", "th"):
            self._end_cell()
            if self._row is None:
                self._row = []
            self._cell = []
            given = dict(attrs)
            self._colspan = _span(given.get("colspan"), MAX_COLUMNS + 1)
            self._rowspan = _span(given.get("rowspan"), 100)
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag == "table":
            if self._depth == 1:
                self._end_row()
            self._depth = max(0, self._depth - 1)
        elif self._depth != 1:
            return
        elif tag in ("td", "th"):
            self._end_cell()
        elif tag == "tr":
            self._end_row()

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def close(self):
        super().close()
        self._end_row()


def _span(value: str | None, most: int) -> int:
    return min(max(int(value), 1), most) if value and value.strip().isdigit() else 1


def _html_rows(contents: bytes) -> list[list[str]]:
    text = _decode(contents)
    reader = _HtmlTables()
    reader.feed(text)
    reader.close()
    return reader.rows


def _non_blank(rows: list[list[str]]) -> int:
    return sum(1 for row in rows for cell in row if cell)


def _pick_sheet(sheets: list[list[list[str]]]) -> list[list[str]]:
    """The sheet that holds the statement: the first whose header is understood,
    else the fullest one."""
    for rows in sheets:
        if _locate(rows)[2] is not None:
            return rows
    return max(sheets, key=_non_blank, default=[])


def read_rows(contents: bytes) -> tuple[str, list[list[str]]]:
    """(kind, rows of text) for a CSV, XLSX, XLS or HTML table. Trailing blank
    rows are dropped; the rows above and between keep their place, so a row number
    in a message is the one the person's spreadsheet shows."""
    if not contents.strip():
        raise SpreadsheetError("This file is empty.")
    kind = sniff_kind(contents)
    if kind is None or kind == "pdf":
        raise SpreadsheetError(
            "This file isn't a statement Finance Copilot can read. Add a PDF, CSV, XLSX or XLS file."
        )
    if kind == "csv":
        rows = _csv_rows(contents)
    elif kind == "html":
        rows = _html_rows(contents)
    elif kind == "xlsx":
        rows = _pick_sheet(_xlsx_sheets(contents))
    else:
        rows = _pick_sheet(_xls_sheets(contents))
    if len(rows) > MAX_ROWS:
        raise SpreadsheetError("This file has too many rows to read as one statement.")
    while rows and not any(rows[-1]):
        rows.pop()
    if not any(any(row) for row in rows):
        raise SpreadsheetError("This file is empty.")
    return kind, rows


# --- finding the table ------------------------------------------------------


def _is_data_row(row: list[str]) -> bool:
    """A row that looks like a payment: a date in one cell and a number in another."""
    has_date = any(generic.parse_date_either_way(c) for c in row if c)
    return has_date and any(
        generic._parse_amount(c) is not None and generic.parse_date_either_way(c) is None for c in row if c
    )


def _locate(rows: list[list[str]]) -> tuple[int | None, int, ColumnMap | None]:
    """(header row, first payment row, the columns the header names) as indexes.
    The header is the first row the PDF path would accept. With none, the first
    payment row is the first row that looks like one, and the header is the row
    above it when that has text in two or more cells (a header written in words
    nothing recognises); with no payment-like row at all there is no table."""
    for index, row in enumerate(rows[:_SAMPLE_ROWS]):
        cmap = generic._map_header(row)
        if cmap is not None:
            # A header written over two rows (a spanning "Amount" above
            # "Withdrawal" and "Deposit"): the last of the rows that read as a header
            # is the one that names the columns.
            while index + 1 < len(rows) and (below := generic._map_header(rows[index + 1])) is not None:
                index, cmap = index + 1, below
            return index, index + 1, cmap
    first = next((i for i, row in enumerate(rows[:_SAMPLE_ROWS]) if _is_data_row(row)), None)
    if first is None:
        return None, 0, None
    header = next(
        (i for i in range(first - 1, -1, -1) if any(rows[i])),
        None,
    )
    if header is not None and sum(1 for c in rows[header] if c) < 2:
        header = None
    return header, first, None


def _letters(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _normalised(cell: str) -> str:
    return re.sub(r"\s+", " ", cell.strip().lower())


@dataclass
class Table:
    """A sheet read as a statement's table: its rows, where the payments start,
    what the columns are called, and what is written above them."""

    kind: str
    rows: list[list[str]]
    header_row: int | None
    first_data: int
    recognised: ColumnMap | None  # the columns the header names, when it names enough
    columns: list[str]  # a label for each column, for the form
    signature: str  # the same for every file with the same header
    preamble: str  # the rows above the table, as text

    @property
    def data_rows(self) -> list[list[str]]:
        return self.rows[self.first_data :]

    @property
    def width(self) -> int:
        return len(self.columns)


def read_table(contents: bytes) -> Table:
    kind, rows = read_rows(contents)
    header_row, first_data, recognised = _locate(rows)
    data = rows[first_data:]
    width = max((len(r) for r in data[:_SAMPLE_ROWS * 4]), default=0)
    header_cells = rows[header_row] if header_row is not None else []
    width = max(width, len(header_cells))
    columns = [
        (header_cells[i].strip() if i < len(header_cells) and header_cells[i].strip() else f"Column {_letters(i)}")
        for i in range(width)
    ]
    if header_row is None:
        signature_source: list = ["no header", width]
    else:
        cells = [_normalised(c) for c in header_cells]
        while cells and not cells[-1]:
            cells.pop()
        signature_source = cells
    signature = hashlib.sha256(json.dumps(signature_source).encode()).hexdigest()
    above = rows[: header_row if header_row is not None else first_data]
    preamble = "\n".join(" ".join(c for c in row if c) for row in above)
    return Table(kind, rows, header_row, first_data, recognised, columns, signature, preamble)


# --- which column is which --------------------------------------------------


def mapping_of(cmap: ColumnMap) -> dict[str, int]:
    """A ColumnMap as {role: column}, the form the person's choice is kept in."""
    found = {role: getattr(cmap, role) for role in ROLES}
    return {role: index for role, index in found.items() if index is not None}


def _sample(table: Table, column: int) -> list[str]:
    cells = []
    for row in table.data_rows:
        if column < len(row) and row[column]:
            cells.append(row[column])
        if len(cells) >= _SAMPLE_ROWS:
            break
    return cells


def _rate(cells: list[str], test) -> float:
    return sum(1 for c in cells if test(c)) / len(cells) if cells else 0.0


def _guess_mapping(table: Table) -> dict[str, int]:
    """A first guess at the columns of a table whose header nothing recognised:
    what the header words name (any of them), then what the columns hold. A guess
    is only where the form starts; the person sees it read and can change it."""
    header = table.rows[table.header_row] if table.header_row is not None else []
    roles = header_roles(header)
    taken = set(roles.values())
    columns = [c for c in range(table.width) if c not in taken]
    samples = {c: _sample(table, c) for c in range(table.width)}

    if "date" not in roles:
        for c in columns:
            if _rate(samples[c], lambda t: generic.parse_date_either_way(t) is not None) >= 0.8:
                roles["date"] = c
                break
    taken = set(roles.values())

    def is_number(text: str) -> bool:
        return generic._parse_amount(text) is not None and generic.parse_date_either_way(text) is None

    numeric = [c for c in range(table.width) if c not in taken and _rate(samples[c], is_number) >= 0.8]
    taken |= set(numeric)
    if "description" not in roles:
        words = [c for c in range(table.width) if c not in taken and samples[c]]
        if words:
            roles["description"] = max(words, key=lambda c: sum(len(t) for t in samples[c]) / len(samples[c]))

    if not any(r in roles for r in ("debit", "credit", "amount")) and numeric:
        signed = [c for c in numeric if any(is_negative(t) for t in samples[c])]
        roles["amount"] = signed[0] if signed else numeric[0]
        rest = [c for c in numeric if c != roles["amount"]]
        if "balance" not in roles and rest:
            roles["balance"] = rest[-1]
    return roles


def starting_mapping(table: Table) -> dict[str, int]:
    """Where the form starts: the header's own reading when it names the columns,
    else a guess from what they hold ({} when there is no table at all)."""
    if table.recognised is not None:
        return mapping_of(table.recognised)
    if table.header_row is None and table.first_data == 0 and not any(_is_data_row(r) for r in table.rows[:_SAMPLE_ROWS]):
        return {}
    return _guess_mapping(table)


def has_payment_rows(table: Table) -> bool:
    """Whether anything in the file looks like a payment (a date and a number on
    one row). A file with none is not a statement, and asking which column is which
    would be a question with no answer."""
    return table.recognised is not None or any(_is_data_row(row) for row in table.rows[:_SAMPLE_ROWS])


def parse_mapping_field(text: str) -> dict[str, int]:
    """The person's column choices as the form sends them: JSON {role: column}."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        value = None
    if not isinstance(value, dict) or any(
        k not in ROLES or isinstance(v, bool) or not isinstance(v, int) for k, v in value.items()
    ):
        raise SpreadsheetError("The column choices couldn't be read. Choose them again.")
    return value


def column_map(table: Table, mapping: dict[str, int]) -> ColumnMap:
    """A ColumnMap from the person's (or the header's) choice, with each way it can
    be wrong said in words."""
    used: dict[int, str] = {}
    for role, index in mapping.items():
        if index < 0 or index >= table.width:
            raise SpreadsheetError(f"The column chosen for {ROLE_WORDS[role]} isn't in the file.")
        if index in used:
            raise SpreadsheetError(
                f"One column can't be more than one thing ({ROLE_WORDS[used[index]]} and {ROLE_WORDS[role]}). "
                "Choose a different column for one of them."
            )
        used[index] = role
    if "date" not in mapping:
        raise SpreadsheetError("Choose which column is the Date.")
    if "description" not in mapping:
        raise SpreadsheetError("Choose which column is the Description.")
    if not any(role in mapping for role in ("debit", "credit", "amount")):
        raise SpreadsheetError("Choose the Paid out, Paid in or Amount column.")
    amount = mapping.get("amount")
    signed = amount is not None and any(
        is_negative(row[amount]) for row in table.data_rows if amount < len(row) and row[amount]
    )
    return ColumnMap(
        date=mapping["date"],
        description=mapping["description"],
        debit=mapping.get("debit"),
        credit=mapping.get("credit"),
        amount=amount,
        drcr=mapping.get("drcr"),
        balance=mapping.get("balance"),
        width=table.width,
        signed=signed,
    )


# --- reading the payments ---------------------------------------------------


@dataclass
class Parsed:
    transactions: list[Transaction]  # oldest first, balances as the file printed them
    has_balance: bool
    account_ref: str | None  # from the rows above the table
    period: tuple[date, date] | None  # from the rows above the table
    bank: str | None  # a bank the rows above the table name


def _day(value: date) -> str:
    return f"{value.day} {calendar.month_abbr[value.month]} {value.year}"


_NUMERIC_DATE_RE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](?:\d{2}|\d{4})$")


def _in_order(days: list[date]) -> bool:
    """Oldest first or newest first all the way: how a bank's file runs."""
    return days == sorted(days) or days == sorted(days, reverse=True)


def date_reading(table: Table, mapping: dict[str, int]) -> tuple[str, bool]:
    """(order, ambiguous) for the file's date column: "dmy" or "mdy", and whether
    the file leaves it open. A value with a part above 12 settles it (a 25 is a
    day). Failing that, when only one reading keeps the dates in order down the
    file, that is the one. When both do, or neither, the file does not say: the
    order is "dmy" (the Indian convention) and ambiguous is True, so the person is
    asked. Dates written with a month name or the year first are never ambiguous."""
    column = mapping.get("date")
    if column is None:
        return "dmy", False
    shapes = []
    # The period the rows above the table print is dated the way the bank prints dates:
    # a day above 12 in it settles the column as much as one in the column does.
    printed = [
        (text, int(found.group(1)), int(found.group(2)))
        for text in period_texts(table.preamble) or ()
        if (found := _NUMERIC_DATE_RE.match(text.strip()))
    ]
    for row in table.data_rows:
        if column < len(row):
            text = generic._TIME_RE.sub("", row[column].strip()).strip()
            found = _NUMERIC_DATE_RE.match(text)
            if found:
                shapes.append((text, int(found.group(1)), int(found.group(2))))
    if not shapes:
        return "dmy", False
    first_big = any(a > 12 for _, a, _ in shapes + printed)
    second_big = any(b > 12 for _, _, b in shapes + printed)
    if first_big or second_big:
        # Both at once is no one order: the rows that do not read the column's
        # usual way are named when it is read.
        return ("mdy" if second_big and not first_big else "dmy"), False
    if all(a == b for _, a, b in shapes):
        return "dmy", False
    readings = {
        order: [d for d in (generic._parse_date(text, order) for text, _, _ in shapes) if d is not None]
        for order in DATE_ORDERS
    }
    fits = [order for order in DATE_ORDERS if len(readings[order]) == len(shapes) and _in_order(readings[order])]
    if len(fits) == 1:
        return fits[0], False
    return "dmy", True


def parse_table(table: Table, mapping: dict[str, int], date_order: str | None = None) -> Parsed:
    """The table's payments, read with these columns and checked: every date must
    read, every payment must have a direction, and where there is a balance column
    each balance must follow from the one before it. `date_order` is the person's
    answer ("dmy" or "mdy") for a date column that does not say which it is; a
    column that does say keeps its own order."""
    cmap = column_map(table, mapping)
    order, ambiguous = date_reading(table, mapping)
    if ambiguous:
        if date_order is None:
            raise DateOrderUnclear()
        if date_order not in DATE_ORDERS:
            raise SpreadsheetError("The date order has to be day first or month first.")
        order = date_order
    result = generic.extract_with_map(table.data_rows, cmap, first_row=table.first_data + 1, date_order=order)

    if result.bad_dates:
        row, text = result.bad_dates[0]
        raise SpreadsheetError(
            f'The date "{text}" in row {row} isn\'t one we can read. '
            "Dates need the day first, like 25/10/2026, or the year first, like 2026-10-25."
        )
    if result.bad_amounts:
        row, text = result.bad_amounts[0]
        raise SpreadsheetError(
            f'The amount "{text}" in row {row} isn\'t one we can read, so it was not guessed. '
            "Amounts look like 1,234.50, 1234.50, (1,234.50), -1,234.50 or 1,234.50 Dr."
        )
    if result.mixed_decimals:
        row, text, before = result.mixed_decimals
        now, was = ("a decimal comma", "a decimal point") if before == "dot" else ("a decimal point", "a decimal comma")
        raise SpreadsheetError(
            f'Row {row} writes "{text}" with {now}, but the rows above it in that column use {was}. '
            "A column has to use one way, or an amount could be read 100 times too large."
        )
    if not result.transactions:
        if result.skipped_unknown_direction:
            raise SpreadsheetError(_no_direction(result.skipped_unknown_direction))
        raise SpreadsheetError(
            "No payments were found in this file. Check that the Date, Description and amount "
            "columns are the right ones."
        )
    if result.skipped_unknown_direction:
        raise SpreadsheetError(_no_direction(result.skipped_unknown_direction))
    if result.breaks:
        b = result.breaks[0]
        where = f"row {b.row}" if b.row else "one row"
        raise SpreadsheetError(
            f"The running balance doesn't add up at {where} ({_day(b.txn_date)}, {rupees(b.amount)}): "
            f"after this payment it should be {rupees(b.expected)}, and the file says {rupees(b.printed)}. "
            "Check that the Balance column is the right one, or that no rows are missing."
        )
    return Parsed(
        transactions=result.transactions,
        has_balance=any(t.running_balance is not None for t in result.transactions),
        account_ref=account_ref_in_text(table.preamble),
        period=period_in_header(table.preamble),
        bank=identify_bank(table.preamble) if table.preamble else None,
    )


def _no_direction(count: int) -> str:
    what = "One payment has" if count == 1 else f"{count} payments have"
    return (
        f"{what} an amount and nothing that says whether it was money in or money out. "
        "Choose a Debit or credit marker column, use separate Paid out and Paid in columns, "
        "or include the Balance column."
    )


def _preview_rows(table: Table, mapping: dict[str, int], order: str | None, limit: int) -> dict:
    try:
        parsed = parse_table(table, mapping, date_order=order)
    except SpreadsheetError as exc:
        return {"rows": [], "error": str(exc)}
    return {
        "rows": [
            {
                "date": t.txn_date.isoformat(),
                "description": t.raw_description,
                "amount": t.amount,
                "direction": t.debit_credit,
                "balance": t.running_balance,
            }
            for t in parsed.transactions[:limit]
        ],
        "error": None,
    }


def preview(table: Table, mapping: dict[str, int], limit: int = 5, date_order: str | None = None) -> dict:
    """The first payments as they would be read with these columns, or the message
    that says why they cannot be: what the form shows beside the columns. Where the
    date column does not say which way round it is, `date_choices` holds the reading
    for each way ({"dmy": ..., "mdy": ...}) and `rows` follows the chosen one, day
    first until the person chooses."""
    try:
        _, ambiguous = date_reading(table, mapping) if "date" in mapping else ("dmy", False)
    except SpreadsheetError:
        ambiguous = False
    if not ambiguous:
        return {**_preview_rows(table, mapping, None, limit), "date_choices": None}
    choices = {order: _preview_rows(table, mapping, order, limit) for order in DATE_ORDERS}
    return {**choices[date_order if date_order in DATE_ORDERS else "dmy"], "date_choices": choices}
