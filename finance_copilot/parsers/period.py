"""The period a statement says it covers, read from its header.

"Statement period: 01/10/2026 to 31/10/2026", "Period: 01 Oct 2026 - 31 Oct 2026",
"From 01-Oct-2026 To 31-Oct-2026", or two dates joined by "to" or a dash under the
statement's title. Dates are day first, as Indian banks print them.

This is what the statement claims, as opposed to the dates of its first and last
payment: a statement for 1 to 31 October whose last payment is on the 28th still
covers the whole month.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from finance_copilot.parsers.bank_identity import header_above_table

_DAY_MONTH_YEAR = r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"
_DAY_MONTH_NAME_YEAR = r"\d{1,2}(?:st|nd|rd|th)?[ \-/.]*[A-Za-z]{3,9}\.?,?[ \-/.]*\d{2,4}"
_MONTH_NAME_DAY_YEAR = r"[A-Za-z]{3,9}\.? \d{1,2},? \d{4}"
_YEAR_MONTH_DAY = r"\d{4}-\d{2}-\d{2}"
_DATE = (
    rf"(?:{_YEAR_MONTH_DAY}|{_DAY_MONTH_YEAR}|{_MONTH_NAME_DAY_YEAR}|{_DAY_MONTH_NAME_YEAR})"
)
# Between the two dates: words, or a hyphen, en dash or long dash. The characters
# are built here so this source holds none of the long ones.
_DASHES = "-" + chr(0x2013) + chr(0x2014)
_TO = rf"\s*(?:(?:to|till|until|through|and)\s*:?|[{_DASHES}])\s*"
_LABEL = (
    r"(?:statement\s+period|period|for\s+the\s+period|statement\s+(?:for|from)|from|between)"
)

_LABELLED = re.compile(rf"\b{_LABEL}\s*[:\-]?\s*({_DATE}){_TO}({_DATE})", re.IGNORECASE)
_BARE = re.compile(rf"(?<![\w/.\-])({_DATE}){_TO}({_DATE})(?![\w/])", re.IGNORECASE)

# No statement covers more than a year; a pair of dates further apart than this
# (a fixed deposit's term, a customer-since range) is something else.
MAX_PERIOD_DAYS = 366

_FORMATS = (
    "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y", "%d.%m.%y",
    "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y", "%d/%b/%Y", "%Y-%m-%d",
    "%b %d %Y", "%B %d %Y",
)
_ORDINAL = re.compile(r"(?<=\d)(?:st|nd|rd|th)\b", re.IGNORECASE)


def _parse_date(text: str) -> date | None:
    cleaned = _ORDINAL.sub("", text).replace(",", " ").replace(".", " ") if re.search(
        r"[A-Za-z]", text
    ) else text
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for fmt in _FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def parse_printed_period(page_text: str) -> tuple[date, date] | None:
    """The (first, last) day a statement says it covers, from its first page's
    text; None when the header says nothing readable. Never raises.

    Only the header is read, the text above the payment table: with no table
    heading the header cannot be told from payment lines, so nothing is read. A
    labelled period ("Statement period", "From ... To ...") wins over a bare pair
    of dates, and a pair spanning more than a year is not a statement period.
    """
    try:
        header = header_above_table(page_text)
        return None if header is None else period_in_header(header)
    except Exception:
        return None


def period_in_header(header: str) -> tuple[date, date] | None:
    """The period a statement's header text says, by the rule above. A spreadsheet's
    rows above its table are its header, so they are read with this too."""
    for pattern in (_LABELLED, _BARE):
        for match in pattern.finditer(header):
            start, end = _parse_date(match.group(1)), _parse_date(match.group(2))
            if start and end and start <= end and (end - start).days < MAX_PERIOD_DAYS:
                return start, end
    return None
