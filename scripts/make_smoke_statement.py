"""Generate the fictional fixture statements in finance_copilot/tests/fixtures/.

sample_statement.pdf is October 2026; sample_statement_nov.pdf is November 2026
and opens on October's closing balance, so uploading both raises no gap warning.
sample_statement_other_bank.pdf is a second account at another bank (its IFSC
names HDFC), October 2026, whose printed period covers the whole month although
its last payment is on the 12th.
sample_statement_overlap.pdf is the same account as sample_statement.pdf for
5 October to 15 November 2026: it repeats the last four October payments and all
of November, with the same running balances, and adds three payments after
November's last one (10, 12 and 15 November). Added after both months, it brings
only those three; added first, the months bring the rest.

sample_statement_transfer_out.pdf (ICICI, account ending 7788) and
sample_statement_transfer_in.pdf (Axis, account ending 1234) are two accounts of
one person, October 2026. Rs 12,000 leaves the first on the 14th ("TO OWN ACCOUNT
XX1234") and arrives in the second on the 15th: a transfer that pairs by itself.
Rs 7,000 leaves the first on the 20th and Rs 7,000 arrives in the second on the
21st with nothing in either line that says they are the same person's money, so
nothing pairs and Teach Me offers the candidate.
sample_statement_neft_out.pdf (SBI, account ending 5566) and
sample_statement_neft_in.pdf (Kotak, account ending 9900) are two more accounts of
one person with a NEFT transfer that carries no sign of being their own: Rs 25,000
leaves the first on the 12th as "NEFT/N123456789/ABHI B/SBIN0001234" and arrives in
the second the same day as "NEFT/N123456789/ABHI B/KKBK0000123". The merchant
catalog labels any NEFT line Transfers / Peer Transfer, so both sides carry a rule
label and nothing pairs: the pair waits in Teach Me, and every figure has to count
it as not sorted yet.

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

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# Without this reportlab stamps the creation time and a random document id into
# every PDF, so each regeneration changed the committed bytes.
rl_config.invariant = 1

FIXTURES = Path(__file__).resolve().parent.parent / "finance_copilot" / "tests" / "fixtures"

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


NOV_ROWS = [
    ("01/11/2026", "UPI/SWIGGY/ORDER/500112233/UPI", 389.00, None),
    ("02/11/2026", "UPI/BMRCL METRO/RECHARGE/500112234", 200.00, None),
    ("03/11/2026", "NEFT/ACME LABS PVT LTD/SALARY NOV", None, 85000.00),
    ("04/11/2026", "UPI/ZEPTO/ORDER/500112235/UPI", 512.75, None),
    ("05/11/2026", "ACH/SIP/EXAMPLE MUTUAL FUND", 5000.00, None),
    ("06/11/2026", "UPI/RAHUL MEHTA/500112236/MOVIE SHARE", None, 450.00),
    ("07/11/2026", "POS/EXAMPLE BOOKSTORE BANGALORE", 1299.00, None),
    ("08/11/2026", "UPI/ELECTRICITY BOARD/BILL/500112237", 1912.50, None),
]


OTHER_BANK_ROWS = [
    ("02/10/2026", "UPI/SWIGGY/ORDER/600112233/UPI", 530.00, None),
    ("03/10/2026", "NEFT/ORBIT CONSULTING LLP/FEES OCT", None, 42000.00),
    ("05/10/2026", "UPI/BMRCL METRO/RECHARGE/600112234", 300.00, None),
    ("08/10/2026", "POS/EXAMPLE PHARMACY BANGALORE", 215.50, None),
    ("10/10/2026", "UPI/RAHUL MEHTA/600112235/TICKETS", 1200.00, None),
    ("12/10/2026", "ACH/SIP/SAMPLE INDEX FUND", 3000.00, None),
]

# The rows of sample_statement.pdf from the 5th on, all of sample_statement_nov.pdf,
# and what the bank printed after them. Its opening balance is the balance after
# the 4th, so every running balance equals the one the two months carry.
OVERLAP_NEW_ROWS = [
    ("10/11/2026", "UPI/SWIGGY/ORDER/500112238/UPI", 276.00, None),
    ("12/11/2026", "UPI/ZEPTO/ORDER/500112239/UPI", 431.25, None),
    ("15/11/2026", "UPI/RAHUL MEHTA/500112240/LUNCH SHARE", None, 320.00),
]


# Two accounts of one fictional person at two fictional-bank IFSCs. The numbers
# the lines name (XX1234) are the other account's, which is what lets a pair be
# made without asking.
TRANSFER_OUT_ROWS = [
    ("02/10/2026", "NEFT/ACME LABS PVT LTD/SALARY OCT", None, 90000.00),
    ("06/10/2026", "UPI/SWIGGY/ORDER/700112233/UPI", 480.00, None),
    ("09/10/2026", "POS/EXAMPLE BOOKSTORE BANGALORE", 650.00, None),
    ("14/10/2026", "IMPS/TO OWN ACCOUNT XX1234/SAVINGS", 12000.00, None),
    ("20/10/2026", "UPI/QUARTZ INFRA/700112235/DEPOSIT", 7000.00, None),
    ("22/10/2026", "UPI/ELECTRICITY BOARD/BILL/700112234", 1730.00, None),
]

# A second export of the first account, as a bank prints it when asked for the second half of the
# month: the rows from the 14th on (the signed 12,000 transfer among them) and one payment the
# month's file did not have. Added after the month, it brings that one payment and no second
# copy of the transfer.
TRANSFER_OUT_LATE_ROWS = TRANSFER_OUT_ROWS[3:] + [
    ("28/10/2026", "UPI/ZEPTO/ORDER/700112236/UPI", 365.00, None),
]

TRANSFER_IN_ROWS = [
    ("05/10/2026", "UPI/ZEPTO/ORDER/800112233/UPI", 310.00, None),
    ("15/10/2026", "IMPS/INWARD/400999111", None, 12000.00),
    ("18/10/2026", "ACH/SIP/SAMPLE INDEX FUND", 2000.00, None),
    ("21/10/2026", "UPI/HELIX TRADERS/800112234/INVOICE", None, 7000.00),
    ("27/10/2026", "POS/EXAMPLE PHARMACY BANGALORE", 245.50, None),
]

# A NEFT transfer between two accounts of one person that the lines do not say is
# their own (the reference and the name are the same on both sides; the numbers
# are not in the text). The merchant catalog labels both lines Transfers / Peer
# Transfer, so the pair is an open candidate that already carries a rule label.
NEFT_OUT_ROWS = [
    ("04/10/2026", "UPI/SWIGGY/ORDER/900112233/UPI", 520.00, None),
    ("12/10/2026", "NEFT/N123456789/ABHI B/SBIN0001234", 25000.00, None),
    ("19/10/2026", "POS/EXAMPLE BOOKSTORE BANGALORE", 780.00, None),
]

NEFT_IN_ROWS = [
    ("03/10/2026", "NEFT/SALARY/ACME LABS PVT LTD", None, 60000.00),
    ("12/10/2026", "NEFT/N123456789/ABHI B/KKBK0000123", None, 25000.00),
    ("21/10/2026", "POS/EXAMPLE PHARMACY BANGALORE", 310.00, None),
]


def build(
    out: Path, opening: float, rows: list, period: str,
    account: str = "XXXX0000", ifsc: str | None = None,
) -> float:
    """Write one statement PDF and return its closing balance."""
    styles = getSampleStyleSheet()
    data = [["Date", "Narration", "Withdrawal", "Deposit", "Balance"]]
    balance = opening
    for date, narration, debit, credit in rows:
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
    doc = SimpleDocTemplate(str(out), pagesize=A4, title="Sample statement (fictional)")
    doc.build([
        Paragraph("Example Bank - Statement of Account (fictional test data)", styles["Title"]),
        Paragraph(
            f"Account: {account} &nbsp; "
            + (f"IFSC: {ifsc} &nbsp; " if ifsc else "")
            + f"Period: {period} &nbsp; Opening balance: {opening:,.2f}",
            styles["Normal"],
        ),
        Spacer(1, 12),
        table,
    ])
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return balance


def build_overlap(out: Path) -> float:
    """The overlapping statement of sample_statement.pdf's account, built from the
    rows of the two month fixtures so its balances match theirs."""
    opening = OPENING
    for _, _, debit, credit in ROWS[:4]:
        opening = round(opening - (debit or 0) + (credit or 0), 2)
    return build(
        out, opening, ROWS[4:] + NOV_ROWS + OVERLAP_NEW_ROWS, "05/10/2026 - 15/11/2026",
    )


def main() -> None:
    closing = build(FIXTURES / "sample_statement.pdf", OPENING, ROWS, "01/10/2026 - 08/10/2026")
    build(FIXTURES / "sample_statement_nov.pdf", closing, NOV_ROWS, "01/11/2026 - 08/11/2026")
    build(
        FIXTURES / "sample_statement_other_bank.pdf", 60000.00, OTHER_BANK_ROWS,
        "01/10/2026 - 31/10/2026", account="XXXX4321", ifsc="HDFC0000123",
    )
    build_overlap(FIXTURES / "sample_statement_overlap.pdf")
    build_transfer_pair()
    build_neft_pair()


def build_neft_pair() -> None:
    """Two more accounts of one person: an unsigned NEFT transfer the catalog labels."""
    build(
        FIXTURES / "sample_statement_neft_out.pdf", 80000.00, NEFT_OUT_ROWS,
        "01/10/2026 - 31/10/2026", account="XXXX5566", ifsc="SBIN0001234",
    )
    build(
        FIXTURES / "sample_statement_neft_in.pdf", 10000.00, NEFT_IN_ROWS,
        "01/10/2026 - 31/10/2026", account="XXXX9900", ifsc="KKBK0000123",
    )


def build_transfer_pair() -> None:
    """The two accounts of one person: a signed transfer between them and an unsigned one."""
    build(
        FIXTURES / "sample_statement_transfer_out.pdf", 40000.00, TRANSFER_OUT_ROWS,
        "01/10/2026 - 31/10/2026", account="XXXX7788", ifsc="ICIC0000456",
    )
    opening = 40000.00
    for _, _, debit, credit in TRANSFER_OUT_ROWS[:3]:
        opening = round(opening - (debit or 0) + (credit or 0), 2)
    build(
        FIXTURES / "sample_statement_transfer_out_late.pdf", opening, TRANSFER_OUT_LATE_ROWS,
        "14/10/2026 - 31/10/2026", account="XXXX7788", ifsc="ICIC0000456",
    )
    build(
        FIXTURES / "sample_statement_transfer_in.pdf", 15000.00, TRANSFER_IN_ROWS,
        "01/10/2026 - 31/10/2026", account="XXXX1234", ifsc="UTIB0000789",
    )


if __name__ == "__main__":
    main()
