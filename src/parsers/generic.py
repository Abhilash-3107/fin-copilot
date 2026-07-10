"""Generic bank statement parser: header-driven column mapping + balance-chain verification.

Works on any text-native PDF whose transactions are laid out as a table with a
recognizable header row (date / description / debit / credit / balance in any
order, or a single amount column with Dr/Cr markers). Correctness is enforced
arithmetically: each row must satisfy balance[i] == balance[i-1] ± amount, so
detect() only claims a PDF when the extracted rows are mathematically verified.

All parsing logic operates on plain row lists so it can be unit-tested without
PDFs; pdfplumber is only used in the thin _extract_tables wrapper.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

import pdfplumber

from src.config import settings
from src.models.transaction import Transaction
from src.parsers.base import StatementParser
from src.parsers.upi import parse_upi_description

logger = logging.getLogger(__name__)

# Header keywords per role, checked in priority order so that e.g.
# "Withdrawal Amt." claims the debit role before the generic amount role.
_ROLE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("debit", ["withdrawal", "debit", "dr amount", "paid out"]),
    ("credit", ["deposit", "credit", "cr amount", "paid in"]),
    ("balance", ["balance"]),
    ("date", ["date"]),
    ("drcr", ["dr/cr", "cr/dr", "dr / cr", "type"]),
    ("description", ["description", "particulars", "narration", "remarks", "transaction details", "details"]),
    ("amount", ["amount", "amt"]),
]

# A header row must resolve at least these roles to be trusted.
_REQUIRED_ROLES = {"date", "description"}

_DATE_FORMATS = [
    "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y",
    "%d %b %Y", "%d %b, %Y", "%d-%b-%Y", "%d-%b-%y", "%d %B %Y",
    "%Y-%m-%d", "%b %d, %Y",
]

_AMOUNT_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_DRCR_RE = re.compile(r"\b(cr|dr|credit|debit)\b\.?", re.IGNORECASE)
_CURRENCY_RE = re.compile(r"(?:₹|inr|rs\.?)", re.IGNORECASE)

# For labeling the statement when a known bank's name appears on page 1.
_BANK_NAMES = [
    "hdfc", "icici", "sbi", "state bank", "axis", "kotak", "yes bank",
    "idfc", "indusind", "punjab national", "canara", "union bank",
    "bank of baroda", "federal", "rbl", "au small finance",
]

_VERIFY_TOLERANCE = 0.011  # rupee rounding slack on balance arithmetic
_MIN_VERIFIED_FRACTION = 0.8
_MIN_TRANSACTIONS = 3


@dataclass
class ColumnMap:
    date: int
    description: int
    debit: int | None = None
    credit: int | None = None
    amount: int | None = None
    drcr: int | None = None
    balance: int | None = None
    width: int = 0

    def usable(self) -> bool:
        has_amount = (self.debit is not None or self.credit is not None or self.amount is not None)
        return has_amount


Direction = Literal["debit", "credit"]


@dataclass
class _Entry:
    txn_date: date
    description: str
    amount: float
    debit_credit: Direction | None  # None = direction unknown, inferred later
    balance: float | None


@dataclass
class ParseResult:
    transactions: list[Transaction] = field(default_factory=list)
    verified_fraction: float = 0.0
    checks: int = 0
    has_balances: bool = False

    @property
    def is_confident(self) -> bool:
        """True when there are enough rows and the balance arithmetic checks out."""
        if len(self.transactions) < _MIN_TRANSACTIONS:
            return False
        if not self.has_balances or self.checks == 0:
            return False
        return self.verified_fraction >= _MIN_VERIFIED_FRACTION


def _norm_header(cell: str) -> str:
    return re.sub(r"[^a-z/ ]", "", cell.lower().replace("\n", " ")).strip()


def _map_header(row: list[str]) -> ColumnMap | None:
    """Try to interpret a row as the table header. Returns None if it isn't one."""
    roles: dict[str, int] = {}
    for idx, raw in enumerate(row):
        cell = _norm_header(raw or "")
        if not cell:
            continue
        for role, keywords in _ROLE_KEYWORDS:
            if role in roles:
                continue
            if any(kw in cell for kw in keywords):
                # Prefer the transaction date over a "value date" column
                if role == "date" and "value" in cell:
                    continue
                roles[role] = idx
                break

    if not _REQUIRED_ROLES.issubset(roles):
        return None

    cmap = ColumnMap(
        date=roles["date"],
        description=roles["description"],
        debit=roles.get("debit"),
        credit=roles.get("credit"),
        amount=roles.get("amount"),
        drcr=roles.get("drcr"),
        balance=roles.get("balance"),
        width=len(row),
    )
    return cmap if cmap.usable() else None


def _parse_date(raw: str) -> date | None:
    cleaned = (raw or "").replace("\n", " ").strip()
    if not cleaned:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(raw: str) -> tuple[float, Direction | None] | None:
    """Parse a money cell. Returns (abs_value, 'debit'|'credit'|None) or None if not numeric."""
    cleaned = _CURRENCY_RE.sub("", (raw or "").replace("\n", " ")).strip()
    if not cleaned:
        return None

    marker: Direction | None = None
    m = _DRCR_RE.search(cleaned)
    if m:
        marker = "credit" if m.group(1).lower().startswith("cr") else "debit"
        cleaned = _DRCR_RE.sub("", cleaned)

    cleaned = cleaned.strip()
    num = _AMOUNT_RE.fullmatch(cleaned.lstrip("+"))
    if num is None:
        return None
    value = float(num.group(0).replace(",", ""))
    return abs(value), marker


def _clean_text(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "")).strip()


def _row_to_entry(row: list[str], cmap: ColumnMap) -> _Entry | str | None:
    """Convert a table row to an entry.

    Returns an _Entry for a transaction row, a str (description fragment) for a
    continuation row, or None for rows to ignore (headers, summaries, blanks).
    """
    if len(row) < cmap.width:
        row = row + [""] * (cmap.width - len(row))

    txn_date = _parse_date(row[cmap.date])
    description = _clean_text(row[cmap.description])

    debit = _parse_amount(row[cmap.debit]) if cmap.debit is not None else None
    credit = _parse_amount(row[cmap.credit]) if cmap.credit is not None else None
    amount = _parse_amount(row[cmap.amount]) if cmap.amount is not None else None
    balance_parsed = _parse_amount(row[cmap.balance]) if cmap.balance is not None else None
    balance = balance_parsed[0] if balance_parsed else None

    if txn_date is None:
        # Wrapped description lines carry text but no date and no amounts
        if description and debit is None and credit is None and amount is None:
            return description
        return None

    if debit is not None and debit[0] > 0:
        return _Entry(txn_date, description, debit[0], "debit", balance)
    if credit is not None and credit[0] > 0:
        return _Entry(txn_date, description, credit[0], "credit", balance)
    if amount is not None and amount[0] > 0:
        direction: Direction | None = amount[1]
        if direction is None and cmap.drcr is not None:
            cell = (row[cmap.drcr] or "").strip().lower()
            if cell.startswith("cr"):
                direction = "credit"
            elif cell.startswith("dr"):
                direction = "debit"
        return _Entry(txn_date, description, amount[0], direction, balance)

    return None  # dated row with no amount (e.g. "Opening Balance" line)


def _chain_score(entries: list[_Entry]) -> tuple[float, int]:
    """Fraction of consecutive balance pairs consistent with the amounts between them."""
    checks = 0
    ok = 0
    prev_balance: float | None = None
    for e in entries:
        if e.balance is None:
            prev_balance = None
            continue
        if prev_balance is not None and e.debit_credit is not None:
            expected = e.amount if e.debit_credit == "credit" else -e.amount
            checks += 1
            if abs((e.balance - prev_balance) - expected) <= _VERIFY_TOLERANCE:
                ok += 1
        prev_balance = e.balance
    return (ok / checks if checks else 0.0), checks


def _date_order_score(entries: list[_Entry]) -> float:
    """Fraction of consecutive date pairs in non-decreasing (chronological) order."""
    pairs = list(zip(entries, entries[1:], strict=False))  # offset by one by design
    if not pairs:
        return 1.0
    good = sum(1 for a, b in pairs if a.txn_date <= b.txn_date)
    return good / len(pairs)


def _infer_directions(entries: list[_Entry]) -> None:
    """Fill unknown debit/credit using the running-balance delta (in place)."""
    prev_balance: float | None = None
    for e in entries:
        if e.debit_credit is None and e.balance is not None and prev_balance is not None:
            e.debit_credit = "credit" if e.balance >= prev_balance else "debit"
        prev_balance = e.balance if e.balance is not None else None


def _swap_directions(entries: list[_Entry]) -> list[_Entry]:
    flipped: dict[Direction, Direction] = {"debit": "credit", "credit": "debit"}
    return [
        _Entry(e.txn_date, e.description, e.amount,
               flipped.get(e.debit_credit) if e.debit_credit else None, e.balance)
        for e in entries
    ]


def _resolve(entries: list[_Entry]) -> tuple[list[_Entry], float, int]:
    """Pick the (order, debit/credit polarity) variant the balance chain agrees with most."""
    best: tuple[float, float, list[_Entry], int] | None = None
    for ordered in (entries, list(reversed(entries))):
        for candidate in (ordered, _swap_directions(ordered)):
            candidate = [
                _Entry(e.txn_date, e.description, e.amount, e.debit_credit, e.balance)
                for e in candidate
            ]
            _infer_directions(candidate)
            frac, checks = _chain_score(candidate)
            key = (frac, _date_order_score(candidate))
            if best is None or key > (best[0], best[1]):
                best = (frac, key[1], candidate, checks)
    assert best is not None
    return best[2], best[0], best[3]


def extract_transactions(tables: list[list[list[str | None]]]) -> ParseResult:
    """Pure-logic core: tables (lists of rows) → verified transactions."""
    cmap: ColumnMap | None = None
    entries: list[_Entry] = []

    for table in tables:
        for raw_row in table:
            if raw_row is None:
                continue
            row = [(cell or "") for cell in raw_row]
            header = _map_header(row)
            if header is not None:
                cmap = header  # first header locks mapping; repeats on later pages are skipped
                continue
            if cmap is None:
                continue
            result = _row_to_entry(row, cmap)
            if isinstance(result, _Entry):
                entries.append(result)
            elif isinstance(result, str) and entries:
                entries[-1].description = f"{entries[-1].description} {result}".strip()

    if not entries:
        return ParseResult()

    entries, verified_fraction, checks = _resolve(entries)
    has_balances = any(e.balance is not None for e in entries)

    transactions = []
    skipped_unknown_direction = 0
    for e in entries:
        if e.debit_credit is None:
            skipped_unknown_direction += 1
            continue
        transactions.append(
            Transaction(
                txn_date=e.txn_date,
                amount=e.amount,
                debit_credit=e.debit_credit,
                raw_description=e.description,
                running_balance=e.balance,
                upi_meta=parse_upi_description(e.description, settings.upi_noise_keywords),
            )
        )
    if skipped_unknown_direction:
        logger.warning("generic parser | %d rows skipped: debit/credit direction undeterminable",
                       skipped_unknown_direction)

    return ParseResult(
        transactions=transactions,
        verified_fraction=verified_fraction,
        checks=checks,
        has_balances=has_balances,
    )


class GenericStatementParser(StatementParser):
    """Fallback parser for banks without a dedicated implementation.

    Only claims a PDF (detect → True) when the extracted rows pass the
    running-balance verification, so it cannot silently ingest wrong amounts.
    """

    version: str = "0.1.0"
    bank_name: str = "generic"

    def __init__(self) -> None:
        self._cache: dict[str, ParseResult] = {}

    def detect(self, pdf_path: str, password: str | None = None) -> bool:
        try:
            result = self._parse_pdf(pdf_path, password)
        except Exception as exc:
            logger.debug("generic parser | detect failed for %s: %s", pdf_path, exc)
            return False
        logger.info(
            "generic parser | detect %s | txns=%d verified=%.0f%% checks=%d",
            pdf_path, len(result.transactions), result.verified_fraction * 100, result.checks,
        )
        return result.is_confident

    def parse(self, pdf_path: str, password: str | None = None) -> list[Transaction]:
        result = self._parse_pdf(pdf_path, password)
        if result.transactions and result.verified_fraction < 1.0:
            logger.warning(
                "generic parser | %s: balance chain verified for %.0f%% of %d checks",
                pdf_path, result.verified_fraction * 100, result.checks,
            )
        return result.transactions

    def _parse_pdf(self, pdf_path: str, password: str | None) -> ParseResult:
        if pdf_path in self._cache:
            return self._cache[pdf_path]

        tables: list[list[list[str | None]]] = []
        first_page_text = ""
        with pdfplumber.open(pdf_path, password=password) as pdf:
            for i, page in enumerate(pdf.pages):
                if i == 0:
                    first_page_text = page.extract_text() or ""
                tables.extend(t for t in page.extract_tables() if t)

        result = extract_transactions(tables)
        if result.transactions:
            self.bank_name = self._detect_bank_name(first_page_text)
        self._cache[pdf_path] = result
        return result

    def _detect_bank_name(self, page_text: str) -> str:
        haystack = page_text.lower()
        for name in _BANK_NAMES:
            if name in haystack:
                return f"{name} (auto)"
        return "generic"
