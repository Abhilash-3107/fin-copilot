"""Pydantic models for Transaction, Statement, and related ingestion types."""
from __future__ import annotations

from datetime import date
from typing import Literal, TypedDict

import ulid
from pydantic import BaseModel, Field


class TxnRow(TypedDict, total=False):
    """Shape of a transactions row as returned by the query helpers (sqlite3.Row → dict).

    Plain dict at runtime; exists so type checkers catch key typos in the pipeline.
    """

    id: str
    statement_id: str
    txn_date: str            # ISO date string as stored in SQLite
    amount: float
    debit_credit: str        # 'debit' | 'credit'
    raw_description: str
    running_balance: float | None
    upi_meta: str | None  # JSON string {"vpa", "ref", "note"}


class Transaction(BaseModel):
    id: str = Field(default_factory=lambda: str(ulid.ULID()))
    statement_id: str | None = None  # filled in by ingest pipeline before DB insert
    txn_date: date
    amount: float = Field(gt=0)         # always positive per schema constraint
    debit_credit: Literal["debit", "credit"]
    raw_description: str
    running_balance: float | None = None
    upi_meta: str | None = None      # JSON string {"note": str|null}, set for UPI transactions


class Statement(BaseModel):
    id: str = Field(default_factory=lambda: str(ulid.ULID()))
    bank_name: str
    parser_version: str
    statement_month: str                # YYYY-MM of period_start (display label)
    period_start: date | None = None # earliest transaction date in the statement
    period_end: date | None = None   # latest transaction date in the statement
    file_sha256: str | None = None   # content hash of the uploaded PDF, for dedup
    opening_balance: float | None = None  # balance before the first transaction
    closing_balance: float | None = None  # balance after the last transaction
    account_ref: str | None = None   # account number as printed on the statement
    account_id: str | None = None    # the account the statement belongs to, set at ingest
    printed_start: date | None = None  # first day the statement says it covers (header), if readable
    printed_end: date | None = None    # last day the statement says it covers (header), if readable
    # What the file held and what of it was new (the rest was already in the
    # account), and the running-balance check of the whole file as parsed.
    rows_in_file: int | None = None
    rows_added: int | None = None
    balance_checked: int | None = None
    balance_consistent: int | None = None
    # Not stored: whether ingest created the account for this statement, and what
    # ingest noticed about the file that the person should read (shown on its row).
    account_is_new: bool = Field(default=False, exclude=True)
    notices: list[str] = Field(default_factory=list, exclude=True)
    # Not stored: how many transfers to or from the user's other accounts ingest paired.
    transfers_paired: int = Field(default=0, exclude=True)
