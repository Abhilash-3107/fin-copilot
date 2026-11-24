"""Generate the fictional spreadsheet statements in finance_copilot/tests/fixtures/.

They are the same fictional accounts as the PDF fixtures made by
make_smoke_statement.py, built from its rows, so a spreadsheet and a PDF of one
month hold the same payments and balances:

sample_statement.csv, sample_statement.xlsx, sample_statement.xls: October 2026
of the account of sample_statement.pdf, with its account number and period above
the table, so each lands in that account without a question.
sample_statement_nov.csv: November 2026 of the same account.
sample_statement_overlap.csv: 5 October to 15 November 2026 of that account, as a
second export of it prints it: narrations shortened, balances kept. Added after
the months it brings only the three payments after November's last.
sample_export_oct.csv and sample_export_nov.csv: another fictional account in a
layout no header word names ("When", "What", "Moved", "Left", one signed
amount), with nothing above the table. The first asks what the columns are; the
second has the same layout and lands without asking.

Run (reportlab for the shared rows, xlwt to write the old .xls format; neither is
needed by the app):

    uv run --with reportlab --with xlwt python scripts/make_spreadsheet_fixtures.py
"""
from __future__ import annotations

import csv
import importlib.util
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("make_smoke_statement", HERE / "make_smoke_statement.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

FIXTURES = smoke.FIXTURES
HEADER = ["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Balance"]

# The other account, in the layout no header word names.
EXPORT_OCT = [
    ("2026-10-02", "UPI/SWIGGY/ORDER/610112233/UPI", -540.00),
    ("2026-10-03", "NEFT/ORBIT CONSULTING LLP/FEES OCT", 42500.00),
    ("2026-10-05", "UPI/BMRCL METRO/RECHARGE/610112234", -310.00),
    ("2026-10-08", "POS/EXAMPLE PHARMACY BANGALORE", -220.50),
    ("2026-10-10", "UPI/RAHUL MEHTA/610112235/TICKETS", -1250.00),
    ("2026-10-12", "ACH/SIP/SAMPLE INDEX FUND", -3100.00),
]
EXPORT_NOV = [
    ("2026-11-02", "UPI/SWIGGY/ORDER/710112233/UPI", -480.00),
    ("2026-11-03", "NEFT/ORBIT CONSULTING LLP/FEES NOV", 42000.00),
    ("2026-11-05", "UPI/BMRCL METRO/RECHARGE/710112234", -300.00),
    ("2026-11-09", "POS/EXAMPLE PHARMACY BANGALORE", -640.25),
]


def with_balances(opening: float, rows: list) -> list[list]:
    """[date, narration, withdrawal, deposit, balance] for rows of (date, narration, w, d)."""
    out, balance = [], opening
    for day, narration, debit, credit in rows:
        balance = round(balance - (debit or 0) + (credit or 0), 2)
        out.append([day, narration, debit, credit, balance])
    return out


def money(value) -> str:
    return "" if value in (None, "") else f"{value:,.2f}"


def preamble(period: str) -> list[list[str]]:
    return [
        ["Example Bank - Statement of Account (fictional test data)"],
        ["Account Number", "XXXX0000"],
        ["Statement Period", period],
        [],
    ]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerows(rows)
    path.write_bytes(out.getvalue().encode("utf-8"))
    print(f"wrote {path} ({path.stat().st_size} bytes)")


def oct_rows() -> tuple[list[list], float]:
    rows = with_balances(smoke.OPENING, smoke.ROWS)
    return rows, rows[-1][4]


def build_csvs() -> None:
    october, closing = oct_rows()
    write_csv(
        FIXTURES / "sample_statement.csv",
        [*preamble("01/10/2026 - 08/10/2026"), HEADER,
         *[[d, n, money(w), money(c), money(b)] for d, n, w, c, b in october]],
    )
    november = with_balances(closing, smoke.NOV_ROWS)
    write_csv(
        FIXTURES / "sample_statement_nov.csv",
        [*preamble("01/11/2026 - 08/11/2026"), HEADER,
         *[[d, n, money(w), money(c), money(b)] for d, n, w, c, b in november]],
    )
    # The overlap statement of the PDF fixtures, as a second export prints it:
    # the same dates, amounts and balances, the narration cut to 18 characters.
    opening = smoke.OPENING
    for _, _, debit, credit in smoke.ROWS[:4]:
        opening = round(opening - (debit or 0) + (credit or 0), 2)
    overlap = with_balances(opening, smoke.ROWS[4:] + smoke.NOV_ROWS + smoke.OVERLAP_NEW_ROWS)
    write_csv(
        FIXTURES / "sample_statement_overlap.csv",
        [*preamble("05/10/2026 - 15/11/2026"), HEADER,
         *[[d, n[:18], money(w), money(c), money(b)] for d, n, w, c, b in overlap]],
    )

    for name, opening, rows in (
        ("sample_export_oct.csv", 52000.00, EXPORT_OCT),
        ("sample_export_nov.csv", None, EXPORT_NOV),
    ):
        if opening is None:
            opening = round(52000.00 + sum(r[2] for r in EXPORT_OCT), 2)
        table, balance = [["When", "What", "Moved", "Left"]], opening
        for day, narration, moved in rows:
            balance = round(balance + moved, 2)
            table.append([day, narration, f"{moved:.2f}", f"{balance:.2f}"])
        write_csv(FIXTURES / name, table)


def build_xlsx() -> None:
    from openpyxl import Workbook

    october, _ = oct_rows()
    book = Workbook()
    book.properties.creator = "fixture"
    book.properties.created = datetime(2026, 11, 1)
    book.properties.modified = datetime(2026, 11, 1)
    ws = book.active
    ws.title = "Statement"
    for row in preamble("01/10/2026 - 08/10/2026"):
        ws.append(row)
    ws.append(HEADER)
    for d, n, w, c, b in october:
        ws.append([datetime.strptime(d, "%d/%m/%Y"), n, w, c, b])
    for cell in ws["A"][5:]:
        cell.number_format = "DD/MM/YYYY"
    raw = io.BytesIO()
    book.save(raw)
    # A zip stamps each entry with the time it was written; fix it, and the order,
    # so regenerating gives the same bytes.
    out = io.BytesIO()
    with zipfile.ZipFile(raw) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for name in sorted(source.namelist()):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            data = source.read(name)
            if name == "docProps/core.xml":
                # openpyxl stamps "modified" with the time of saving.
                data = re.sub(
                    rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>2026-11-01T00:00:00Z", data
                )
            target.writestr(info, data)
    path = FIXTURES / "sample_statement.xlsx"
    path.write_bytes(out.getvalue())
    print(f"wrote {path} ({path.stat().st_size} bytes)")


def build_xls() -> None:
    import xlwt

    october, _ = oct_rows()
    book = xlwt.Workbook()
    ws = book.add_sheet("Statement")
    dates = xlwt.easyxf(num_format_str="DD/MM/YYYY")
    r = 0
    for row in preamble("01/10/2026 - 08/10/2026"):
        for c, value in enumerate(row):
            ws.write(r, c, value)
        r += 1
    for c, value in enumerate(HEADER):
        ws.write(r, c, value)
    for d, n, w, cr, b in october:
        r += 1
        ws.write(r, 0, datetime.strptime(d, "%d/%m/%Y"), dates)
        ws.write(r, 1, n)
        if w:
            ws.write(r, 2, w)
        if cr:
            ws.write(r, 3, cr)
        ws.write(r, 4, b)
    path = FIXTURES / "sample_statement.xls"
    book.save(str(path))
    print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    build_csvs()
    build_xlsx()
    build_xls()
