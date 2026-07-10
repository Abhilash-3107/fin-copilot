"""StatementParser ABC: parse PDF to transactions, optional detect() for registry selection."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod

from src.models.transaction import Transaction

# "Account No. 3250508074", "A/c Number: XXXX-1234", "Account #: 1234567890" —
# tolerant of masked digits so partial numbers still distinguish accounts, but
# at least one real digit so a fully-redacted header yields None.
_ACCOUNT_REF_RE = re.compile(
    r"(?:account|a/c)\s*(?:no\.?|number|#)?\s*[:\-]?\s*(?=[0-9xX*\-]*[0-9])([0-9xX*][0-9xX*\-]{5,19})"
)


class StatementParser(ABC):
    version: str = "0.0.0"
    bank_name: str = ""

    @abstractmethod
    def parse(self, pdf_path: str, password: str | None = None) -> list[Transaction]:
        """Extract transactions from a bank statement PDF."""
        ...

    def detect(self, pdf_path: str, password: str | None = None) -> bool:
        """Return True if this parser can handle the given PDF."""
        return False

    def extract_account_ref(self, pdf_path: str, password: str | None = None) -> str | None:
        """Best-effort account number from the statement header; None when not found.

        Balance continuity is only meaningful within one account, so this is
        what lets two banks (or two accounts at one bank) coexist without
        cross-account warnings. Never raises: an unreadable header degrades to
        None, which the continuity check treats as "unknown account".
        """
        try:
            import pdfplumber

            with pdfplumber.open(pdf_path, password=password) as pdf:
                text = pdf.pages[0].extract_text() or ""
        except Exception:
            return None
        match = _ACCOUNT_REF_RE.search(text.lower())
        return match.group(1).upper() if match else None
