"""Maps bank_name (and/or detect()) to concrete StatementParser implementations."""
from __future__ import annotations

from src.parsers.base import StatementParser
from src.parsers.banks.kotak import KotakParser
from src.parsers.generic import GenericStatementParser

# Ordered list — detect() is tried in this order; first match wins.
# Dedicated bank parsers go first; the generic balance-verified parser is the
# last-resort fallback and must stay at the end.
_PARSERS: list[type[StatementParser]] = [
    KotakParser,
    GenericStatementParser,
]

_BY_NAME: dict[str, type[StatementParser]] = {
    cls.bank_name: cls for cls in _PARSERS
}


def get_parser(bank_name: str) -> StatementParser:
    """Return a parser instance for the given bank name. Raises KeyError if unknown."""
    return _BY_NAME[bank_name.lower()]()


def detect_parser(pdf_path: str, password: str | None = None) -> StatementParser | None:
    """Return the first parser whose detect() claims the PDF, or None."""
    for cls in _PARSERS:
        inst = cls()
        if inst.detect(pdf_path, password=password):
            return inst
    return None
