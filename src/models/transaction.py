"""Pydantic models for Transaction, Statement, and related ingestion types."""
from __future__ import annotations

import ulid
from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field


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
    statement_month: str                # YYYY-MM format
    file_sha256: Optional[str] = None   # content hash of the uploaded PDF, for dedup
