"""Kotak bank statement PDF parser.

Kotak has shipped two statement layouts:

- Legacy (through early 2026): six columns, dates like "01 Feb, 2026", and
  "Kotak" in the page-one text.
- Current (from mid 2026): a numbered "#" column, dates like "01 Jul 2026",
  separate Withdrawal (Dr.) / Deposit (Cr.) columns, and the bank's name only
  in the logo image, so the IFSC code is what identifies it.

The legacy layout keeps its fixed-column reader. Anything else goes through
the generic header-driven reader, which only accepts the rows when the running
balance reconciles, so a future layout change fails loudly instead of
ingesting wrong amounts.
"""
from __future__ import annotations

import re
import warnings
from datetime import date, datetime

import pdfplumber

from finance_copilot.config import settings
from finance_copilot.models.transaction import Transaction
from finance_copilot.parsers.bank_identity import identify_bank
from finance_copilot.parsers.base import StatementParser
from finance_copilot.parsers.generic import extract_transactions
from finance_copilot.parsers.upi import parse_upi_description

DATE_PATTERN = re.compile(r"^\d{1,2}\s+\w{3},\s+\d{4}$")
# The statement's closing-balance label is extracted into the description cell
# of the last transaction row ("UPI/ZOMATO/122406278840/UPI Closing Balance").
# Requires preceding text, so a standalone balance row is left for ingest's
# balance-artifact handling.
_TRAILING_BALANCE_LABEL = re.compile(r"\s+(?:closing|opening)\s+balance\s*$", re.IGNORECASE)
DATE_FORMAT = "%d %b, %Y"

COL_DATE = 0
COL_DESC = 1
COL_REF = 2
COL_DEBIT = 3
COL_CREDIT = 4
COL_BALANCE = 5


class KotakParser(StatementParser):
    version: str = "1.1.0"
    bank_name: str = "kotak"

    def detect(self, pdf_path: str, password: str | None = None) -> bool:
        try:
            with pdfplumber.open(pdf_path, password=password) as pdf:
                text = pdf.pages[0].extract_text() or ""
        except Exception:
            return False
        return identify_bank(text) == "kotak"

    def parse(self, pdf_path: str, password: str | None = None) -> list[Transaction]:
        with pdfplumber.open(pdf_path, password=password) as pdf:
            pages = [
                (page.extract_table(), [t for t in page.extract_tables() if t])
                for page in pdf.pages
            ]

        transactions = self._parse_legacy([main for main, _ in pages if main])
        if transactions:
            return transactions
        return self._parse_current([t for _, tables in pages for t in tables])

    def _parse_legacy(self, tables: list[list[list[str | None]]]) -> list[Transaction]:
        transactions: list[Transaction] = []
        for table in tables:
            for row in table:
                if row is None:
                    continue
                row = [cell or "" for cell in row]
                if not self._is_transaction_row(row):
                    continue
                try:
                    transactions.append(self._row_to_transaction(row))
                except Exception as exc:
                    warnings.warn(f"KotakParser: skipping row {row!r}: {exc}", stacklevel=2)
        return transactions

    def _parse_current(self, tables: list[list[list[str | None]]]) -> list[Transaction]:
        result = extract_transactions(tables)
        if not result.is_confident:
            raise ValueError(
                "This Kotak statement's layout isn't one I can read reliably yet "
                f"(read {len(result.transactions)} rows, running balance matched "
                f"{result.verified_fraction:.0%} of them)."
            )
        for txn in result.transactions:
            cleaned = self._clean_description(txn.raw_description)
            if cleaned != txn.raw_description:
                txn.raw_description = cleaned
                txn.upi_meta = parse_upi_description(cleaned, settings.upi_noise_keywords)
        return result.transactions

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

    def _clean_description(self, raw: str) -> str:
        return _TRAILING_BALANCE_LABEL.sub("", self._clean_text(raw))

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

        raw_description = self._clean_description(row[COL_DESC])
        return Transaction(
            txn_date=self._parse_date(row[COL_DATE]),
            amount=amount,
            debit_credit=debit_credit,
            raw_description=raw_description,
            running_balance=self._parse_balance(row[COL_BALANCE]),
            upi_meta=parse_upi_description(raw_description, settings.upi_noise_keywords),
        )
