"""Transaction routes: list with filters, get by id with optional annotation, rename, note."""
from __future__ import annotations

import csv
import io
import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator

from finance_copilot.api.deps import get_db
from finance_copilot.db.queries.accounts import with_account
from finance_copilot.db.queries.app_settings import get_dev_mode
from finance_copilot.db.queries.common import UNCATEGORIZED, parse_string_list, waiting_on_pair_sql
from finance_copilot.db.queries.payee import (
    MAX_NAME_LENGTH,
    MAX_NOTE_LENGTH,
    set_payee_name,
    set_payment_name,
    set_payment_note,
    with_payee,
)
from finance_copilot.db.queries.transactions import (
    list_transaction_facets,
    list_transactions,
    payment_months,
    summarize_transactions,
)
from finance_copilot.db.queries.transfers import with_transfer

router = APIRouter()


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    parts = [p for p in value.split(",") if p]
    return parts or None


def _known_account(conn: sqlite3.Connection, account_id: str | None) -> str | None:
    """The account to limit to, or 404 when there is none by that id: a stale filter
    must say so, not show an empty ledger."""
    if account_id and not conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Account not found")
    return account_id or None


def _filters(
    statement_id: str | None = None,
    account_id: str | None = Query(None, description="limit to one account's payments"),
    month: str | None = None,
    unannotated: bool = False,
    q: str | None = Query(None, description="substring match on description, merchant, UPI note, and the names and notes the user wrote"),
    category: str | None = Query(None, description="comma-separated annotation categories; Uncategorized also matches unannotated rows"),
    source: str | None = Query(None, description="comma-separated annotation sources"),
    merchant: str | None = Query(None, description="exact annotated merchant"),
    payee_key: list[str] | None = Query(None, description="payee grouping key (repeat for several), as Money Map rows carry them"),
    person: str | None = Query(None, description="match-token of a person; filters to their matched transactions"),
    min_amount: float | None = Query(None, description="minimum transaction amount"),
    max_amount: float | None = Query(None, description="maximum transaction amount"),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """The filter set shared by the list and its summary."""
    return {
        "statement_id": statement_id,
        "account_id": _known_account(conn, account_id),
        "month": month,
        "unannotated": unannotated,
        "q": q,
        "categories": _split_csv(category),
        "sources": _split_csv(source),
        "merchant": merchant,
        "payee_keys": payee_key,
        "person_token": person,
        "min_amount": min_amount,
        "max_amount": max_amount,
    }


@router.get("")
def get_transactions(
    filters: dict = Depends(_filters),
    sort: str = Query("date", pattern="^(date|amount)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    include: str | None = Query(None, description="'annotation' joins each row's annotation"),
    after: str | None = Query(None, description="cursor: id of the last row of the previous page"),
    limit: int | None = Query(None, ge=1, le=1000),
    conn: sqlite3.Connection = Depends(get_db),
):
    rows = list_transactions(
        conn,
        **filters,
        include_annotation=include == "annotation",
        sort=sort,
        order=order,
        after=after,
        limit=limit,
    )
    if include == "annotation":
        for row in rows:
            row["tags"] = parse_string_list(row.get("tags")) if row.get("annotation_id") else []
    return with_transfer(conn, with_payee(conn, with_account(conn, rows)))


@router.get("/summary")
def get_transactions_summary(
    filters: dict = Depends(_filters),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Count and totals of every row the same filters match on the list, so a
    filtered view can say what it adds up to."""
    return summarize_transactions(conn, **filters)


@router.get("/months")
def get_payment_months(conn: sqlite3.Connection = Depends(get_db)):
    """The months that have payments, oldest first: what the period picker offers.
    A statement can cover several months or part of one, so these come from the
    payments themselves."""
    return {"months": payment_months(conn)}


@router.get("/facets")
def get_transaction_facets(
    statement_id: str | None = None,
    account_id: str | None = None,
    month: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Distinct annotation categories/sources in scope, for filter dropdowns."""
    return list_transaction_facets(
        conn, statement_id=statement_id, month=month, account_id=_known_account(conn, account_id)
    )


_EXPORT_COLUMNS = [
    "id", "txn_date", "amount", "debit_credit", "raw_description",
    "running_balance", "category", "subcategory", "merchant", "tags",
    "confidence", "source", "payee", "note",
]


# A spreadsheet runs a cell that starts with one of these as a formula, and
# payees, notes and printed lines are text the user (or a stranger paying them)
# typed. A leading apostrophe makes the cell plain text; the apostrophe itself is
# not shown in a spreadsheet.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value):
    """A cell safe to open in a spreadsheet. Numbers pass through as numbers."""
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


@router.get("/export")
def export_transactions(
    format: str = Query("csv", pattern="^(csv|json)$"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Download every transaction with its current annotation as CSV or JSON.

    A user-facing "your data is portable" export; annotation fields are flattened
    onto each row and tags are joined so both formats are flat and spreadsheet-ready.
    `category` is the category the app counts the payment under.
    """
    rows = with_payee(conn, list_transactions(conn, include_annotation=True))
    records = []
    for r in rows:
        tags = parse_string_list(r.get("tags")) if r.get("annotation_id") else []
        # The category the app counts the payment under, so an export adds up to the
        # figures: a guess held back as waiting on a pair is Uncategorized.
        waiting = r["waiting_on_pair"]
        records.append({
            "id": r["id"],
            "txn_date": r["txn_date"],
            "amount": r["amount"],
            "debit_credit": r["debit_credit"],
            "raw_description": r["raw_description"],
            "running_balance": r.get("running_balance"),
            "category": UNCATEGORIZED if waiting else r.get("category"),
            "subcategory": None if waiting else r.get("subcategory"),
            "merchant": r.get("merchant"),
            "tags": ",".join(tags),
            "confidence": r.get("confidence"),
            "source": r.get("source"),
            "payee": r["payee"],
            "note": r["note"],
        })

    if format == "json":
        return JSONResponse(
            content=records,
            headers={"Content-Disposition": "attachment; filename=transactions.json"},
        )

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_EXPORT_COLUMNS)
    writer.writeheader()
    writer.writerows({k: _csv_cell(v) for k, v in r.items()} for r in records)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


@router.get("/{transaction_id}")
def get_transaction(
    transaction_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    row = conn.execute(
        f"""
        SELECT t.*, a.id AS annotation_id, a.merchant, a.category, a.subcategory,
               a.tags, a.confidence, a.source, a.annotated_at, a.reasoning,
               {waiting_on_pair_sql('a')} AS waiting_on_pair
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE t.id = ?
        """,
        (transaction_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    result = with_transfer(conn, with_payee(conn, with_account(conn, [dict(row)])))[0]
    result["waiting_on_pair"] = bool(result["waiting_on_pair"])
    if result.get("upi_meta"):
        result["upi_meta"] = json.loads(result["upi_meta"])
    result["tags"] = parse_string_list(result.get("tags")) if result.get("annotation_id") else []
    # Dev mode: surface the captured reasoning trace; otherwise drop the raw
    # column so the UI never sees it. Older rows have reasoning=NULL → None.
    raw = result.pop("reasoning", None)
    if get_dev_mode(conn):
        try:
            result["reasoning"] = json.loads(raw) if raw else None
        except (TypeError, json.JSONDecodeError):
            result["reasoning"] = None
    return result


def _within(value: str | None, cap: int) -> str | None:
    """The cap is on what gets stored: the trimmed text, counted in characters
    (the page counts the same way, so what it lets you type the API accepts)."""
    if value is not None and len(value.strip()) > cap:
        raise ValueError(f"must be at most {cap} characters")
    return value


class NameBody(BaseModel):
    """A name for a payment or a payee; null or blank clears it."""
    name: str | None = None

    @field_validator("name")
    @classmethod
    def _name_cap(cls, value):
        return _within(value, MAX_NAME_LENGTH)


def _payment_or_404(conn: sqlite3.Connection, transaction_id: str):
    row = conn.execute(
        "SELECT id, counterparty_key FROM transactions WHERE id = ?", (transaction_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return row


@router.put("/{transaction_id}/name")
def rename_payment(
    transaction_id: str, body: NameBody, conn: sqlite3.Connection = Depends(get_db),
):
    """Name this one payment. Clearing it falls back to the payee's name."""
    _payment_or_404(conn, transaction_id)
    set_payment_name(conn, transaction_id, body.name)
    conn.commit()
    return get_transaction(transaction_id, conn)


class NoteBody(BaseModel):
    """A note on a payment; null or blank clears it."""
    note: str | None = None

    @field_validator("note")
    @classmethod
    def _note_cap(cls, value):
        return _within(value, MAX_NOTE_LENGTH)


@router.put("/{transaction_id}/note")
def write_payment_note(
    transaction_id: str, body: NoteBody, conn: sqlite3.Connection = Depends(get_db),
):
    """Write a note on this one payment. It shows wherever the UPI note does;
    clearing it brings the UPI note back."""
    _payment_or_404(conn, transaction_id)
    set_payment_note(conn, transaction_id, body.note)
    conn.commit()
    return get_transaction(transaction_id, conn)


@router.put("/{transaction_id}/payee-name")
def rename_payee(
    transaction_id: str, body: NameBody, conn: sqlite3.Connection = Depends(get_db),
):
    """Name every payment to this payment's payee, past and future.

    A payment's own name, this one's included, keeps winning: the payee name
    never clears a name the user gave one payment.
    """
    row = _payment_or_404(conn, transaction_id)
    if row["counterparty_key"] is None:
        raise HTTPException(status_code=409, detail="This payment has no payee to rename.")
    set_payee_name(conn, row["counterparty_key"], body.name)
    conn.commit()
    return get_transaction(transaction_id, conn)
