"""Generate finance_copilot/tests/fixtures/sample_statement.pdf.

A small, entirely fictional bank statement that the generic parser accepts:
a ruled table (pdfplumber's default lines strategy) with date / narration /
withdrawal / deposit / balance columns whose running balance checks out on
every row. The release workflow's 3-OS smoke test uploads it through the real
`uvx finance-copilot` command, so it must stay parseable.

reportlab is only needed to regenerate the file, not by the app:

    uv run --with reportlab python scripts/make_smoke_statement.py
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).resolve().parent.parent / "finance_copilot" / "tests" / "fixtures" / "sample_statement.pdf"

OPENING = 25000.00
# (date, narration, withdrawal, deposit)
ROWS = [
    ("01/10/2026", "UPI/SWIGGY/ORDER/400112233/UPI", 412.00, None),
    ("02/10/2026", "UPI/BMRCL METRO/RECHARGE/400112234", 200.00, None),
    ("03/10/2026", "NEFT/ACME LABS PVT LTD/SALARY OCT", None, 85000.00),
    ("04/10/2026", "UPI/ZEPTO/ORDER/400112235/UPI", 643.50, None),
    ("05/10/2026", "ACH/SIP/EXAMPLE MUTUAL FUND", 5000.00, None),
    ("06/10/2026", "UPI/RAHUL MEHTA/400112236/DINNER SHARE", None, 750.00),
    ("07/10/2026", "POS/EXAMPLE BOOKSTORE BANGALORE", 899.00, None),
    ("08/10/2026", "UPI/ELECTRICITY BOARD/BILL/400112237", 1840.25, None),
]


def main() -> None:
    styles = getSampleStyleSheet()
    data = [["Date", "Narration", "Withdrawal", "Deposit", "Balance"]]
    balance = OPENING
    for date, narration, debit, credit in ROWS:
        balance = round(balance - (debit or 0) + (credit or 0), 2)
        data.append([
            date,
            narration,
            f"{debit:,.2f}" if debit else "",
            f"{credit:,.2f}" if credit else "",
            f"{balance:,.2f}",
        ])

    table = Table(data, colWidths=[70, 220, 70, 70, 75], repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ]))
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, title="Sample statement (fictional)")
    doc.build([
        Paragraph("Example Bank - Statement of Account (fictional test data)", styles["Title"]),
        Paragraph(f"Account: XXXX0000 &nbsp; Period: 01/10/2026 - 08/10/2026 &nbsp; Opening balance: {OPENING:,.2f}", styles["Normal"]),
        Spacer(1, 12),
        table,
    ])
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
