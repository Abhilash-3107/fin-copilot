"""Work out which bank issued a statement from its first-page text.

The statement header's IFSC code is the most reliable signal: it names the
account's own branch, and every bank prints it. Bank names in free text are a
weaker signal, because transaction descriptions also name the *counterparty's*
bank ("UPI/ABHILASH BORA/HDFC/..." on a Kotak statement). So names are only
searched in the header, the text above the first transaction-table heading.
"""
from __future__ import annotations

import re

# IFSC bank prefix -> canonical bank key (the prefix is the first 4 letters).
_IFSC_PREFIXES: dict[str, str] = {
    "HDFC": "hdfc",
    "ICIC": "icici",
    "SBIN": "sbi",
    "UTIB": "axis",
    "KKBK": "kotak",
    "YESB": "yes bank",
    "IDFB": "idfc",
    "INDB": "indusind",
    "PUNB": "punjab national",
    "CNRB": "canara",
    "UBIN": "union bank",
    "BARB": "bank of baroda",
    "FDRL": "federal",
    "RATN": "rbl",
    "AUBL": "au small finance",
}

# Free-text names -> canonical bank key. Matched on word boundaries so "sbi"
# does not fire inside "SBIN" or a merchant name.
_BANK_NAMES: dict[str, str] = {
    "hdfc": "hdfc",
    "icici": "icici",
    "sbi": "sbi",
    "state bank": "sbi",
    "axis": "axis",
    "kotak": "kotak",
    "yes bank": "yes bank",
    "idfc": "idfc",
    "indusind": "indusind",
    "punjab national": "punjab national",
    "canara": "canara",
    "union bank": "union bank",
    "bank of baroda": "bank of baroda",
    "federal": "federal",
    "rbl": "rbl",
    "au small finance": "au small finance",
}
_NAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in sorted(_BANK_NAMES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# "IFSC Code KKBK0001234", "IFSC: HDFC0000123". The 5th character of an IFSC
# is always 0. Requiring the label keeps counterparty bank codes in UPI
# descriptions ("/KKBK/") from matching.
_IFSC_RE = re.compile(r"\bIFSC(?:\s*code)?\s*[:\-.]?\s*([A-Z]{4})0[A-Z0-9]{6}\b", re.IGNORECASE)

# The line where the transaction table starts; everything above is header.
_TABLE_HEADING_RE = re.compile(
    r"^.*\b(?:date)\b.*\b(?:description|particulars|narration|details)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


def identify_bank(page_text: str) -> str | None:
    """Return the canonical bank key ("kotak", "hdfc", ...) or None if unknown."""
    for match in _IFSC_RE.finditer(page_text):
        bank = _IFSC_PREFIXES.get(match.group(1).upper())
        if bank:
            return bank

    heading = _TABLE_HEADING_RE.search(page_text)
    header = page_text[: heading.start()] if heading else page_text
    match = _NAME_RE.search(header)
    return _BANK_NAMES[match.group(1).lower()] if match else None
