"""StatementParser ABC: parse PDF to transactions, optional detect() for registry selection."""
from __future__ import annotations

from abc import ABC, abstractmethod
from financebot.models.transaction import Transaction


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
