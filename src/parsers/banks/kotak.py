"""Kotak bank statement PDF parser."""
from __future__ import annotations

import re
import warnings
from datetime import date, datetime

import pdfplumber

from src.config import settings
from src.models.transaction import Transaction
from src.parsers.base import StatementParser
from src.parsers.upi import parse_upi_description

DATE_PATTERN = re.compile(r"^\d{1,2}\s+\w{3},\s+\d{4}$")
DATE_FORMAT = "%d %b, %Y"

COL_DATE = 0
COL_DESC = 1
COL_REF = 2
COL_DEBIT = 3
COL_CREDIT = 4
COL_BALANCE = 5


class KotakParser(StatementParser):
    version: str = "1.0.0"
    bank_name: str = "kotak"

    def detect(self, pdf_path: str, password: str | None = None) -> bool:
        try:
            with pdfplumber.open(pdf_path, password=password) as pdf:
                text = pdf.pages[0].extract_text() or ""
                return "KOTAK" in text.upper()
        except Exception:
            return False

    def parse(self, pdf_path: str, password: str | None = None) -> list[Transaction]:
        transactions: list[Transaction] = []
        with pdfplumber.open(pdf_path, password=password) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                for row in table:
                    if row is None:
                        continue
                    row = [cell or "" for cell in row]
                    if not self._is_transaction_row(row):
                        continue
                    try:
                        transactions.append(self._row_to_transaction(row))
                    except Exception as exc:
                        warnings.warn(f"KotakParser: skipping row {row!r}: {exc}")
        return transactions

    def _is_transaction_row(self, row: list) -> bool:
        if len(row) < 6:
            return False
        return bool(DATE_PATTERN.match(row[COL_DATE].strip()))

    def _parse_date(self, raw: str) -> date:
        return datetime.strptime(raw.strip(), DATE_FORMAT).date()

    def _parse_amount(self, raw: str) -> float:
        return float(raw.strip().lstrip("+-").replace(",", ""))

    def _parse_balance(self, raw: str) -> float | None:
        cleaned = raw.strip().replace(",", "")
        return float(cleaned) if cleaned else None

    def _clean_text(self, raw: str) -> str:
        return raw.replace("\n", " ").strip()

    def _row_to_transaction(self, row: list) -> Transaction:
        debit_raw = row[COL_DEBIT].strip()
        credit_raw = row[COL_CREDIT].strip()

        if debit_raw:
            amount = self._parse_amount(debit_raw)
            debit_credit = "debit"
        elif credit_raw:
            amount = self._parse_amount(credit_raw)
            debit_credit = "credit"
        else:
            raise ValueError("Row has neither debit nor credit amount")

        raw_description = self._clean_text(row[COL_DESC])
        return Transaction(
            txn_date=self._parse_date(row[COL_DATE]),
            amount=amount,
            debit_credit=debit_credit,
            raw_description=raw_description,
            running_balance=self._parse_balance(row[COL_BALANCE]),
            upi_meta=parse_upi_description(raw_description, settings.upi_noise_keywords),
        )
