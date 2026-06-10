"""Pydantic models for Transaction, Statement, and related ingestion types."""
from __future__ import annotations

import ulid
from datetime import date
from typing import Literal, Optional, TypedDict
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
    running_balance: Optional[float]
    upi_meta: Optional[str]  # JSON string {"vpa", "ref", "note"}


class Transaction(BaseModel):
    id: str = Field(default_factory=lambda: str(ulid.ULID()))
    statement_id: Optional[str] = None  # filled in by ingest pipeline before DB insert
    txn_date: date
    amount: float = Field(gt=0)         # always positive per schema constraint
    debit_credit: Literal["debit", "credit"]
    raw_description: str
    running_balance: Optional[float] = None
    upi_meta: Optional[str] = None      # JSON string {"note": str|null}, set for UPI transactions


class Statement(BaseModel):
    id: str = Field(default_factory=lambda: str(ulid.ULID()))
    bank_name: str
    parser_version: str
    statement_month: str                # YYYY-MM of period_start (display label)
    period_start: Optional[date] = None # earliest transaction date in the statement
    period_end: Optional[date] = None   # latest transaction date in the statement
    file_sha256: Optional[str] = None   # content hash of the uploaded PDF, for dedup
