"""Transaction routes: list with filters, get by id with optional annotation, rename."""
from __future__ import annotations

import csv
import io
import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from finance_copilot.api.deps import get_db
from finance_copilot.db.queries.app_settings import get_dev_mode
from finance_copilot.db.queries.common import parse_string_list
from finance_copilot.db.queries.payee import (
    MAX_NAME_LENGTH,
    set_payee_name,
    set_payment_name,
    with_payee,
)
from finance_copilot.db.queries.transactions import (
    list_transaction_facets,
    list_transactions,
    summarize_transactions,
)

router = APIRouter()


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    parts = [p for p in value.split(",") if p]
    return parts or None


def _filters(
    statement_id: str | None = None,
    month: str | None = None,
    unannotated: bool = False,
    q: str | None = Query(None, description="substring match on description, merchant, UPI note, and the names the user gave"),
    category: str | None = Query(None, description="comma-separated annotation categories; Uncategorized also matches unannotated rows"),
    source: str | None = Query(None, description="comma-separated annotation sources"),
    merchant: str | None = Query(None, description="exact annotated merchant"),
    payee_key: list[str] | None = Query(None, description="payee grouping key (repeat for several), as Money Map rows carry them"),
    person: str | None = Query(None, description="match-token of a person; filters to their matched transactions"),
    min_amount: float | None = Query(None, description="minimum transaction amount"),
    max_amount: float | None = Query(None, description="maximum transaction amount"),
) -> dict:
    """The filter set shared by the list and its summary."""
    return {
        "statement_id": statement_id,
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
    return with_payee(conn, rows)


@router.get("/summary")
def get_transactions_summary(
    filters: dict = Depends(_filters),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Count and totals of every row the same filters match on the list, so a
    filtered view can say what it adds up to."""
    return summarize_transactions(conn, **filters)


@router.get("/facets")
def get_transaction_facets(
    statement_id: str | None = None,
    month: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Distinct annotation categories/sources in scope, for filter dropdowns."""
    return list_transaction_facets(conn, statement_id=statement_id, month=month)


_EXPORT_COLUMNS = [
    "id", "txn_date", "amount", "debit_credit", "raw_description",
    "running_balance", "category", "subcategory", "merchant", "tags",
    "confidence", "source", "payee",
]


@router.get("/export")
def export_transactions(
    format: str = Query("csv", pattern="^(csv|json)$"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Download every transaction with its current annotation as CSV or JSON.

    A user-facing "your data is portable" export; annotation fields are flattened
    onto each row and tags are joined so both formats are flat and spreadsheet-ready.
    """
    rows = with_payee(conn, list_transactions(conn, include_annotation=True))
    records = []
    for r in rows:
        tags = parse_string_list(r.get("tags")) if r.get("annotation_id") else []
        records.append({
            "id": r["id"],
            "txn_date": r["txn_date"],
            "amount": r["amount"],
            "debit_credit": r["debit_credit"],
            "raw_description": r["raw_description"],
            "running_balance": r.get("running_balance"),
            "category": r.get("category"),
            "subcategory": r.get("subcategory"),
            "merchant": r.get("merchant"),
            "tags": ",".join(tags),
            "confidence": r.get("confidence"),
            "source": r.get("source"),
            "payee": r["payee"],
        })

    if format == "json":
        return JSONResponse(
            content=records,
            headers={"Content-Disposition": "attachment; filename=transactions.json"},
        )

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_EXPORT_COLUMNS)
    writer.writeheader()
    writer.writerows(records)
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
        """
        SELECT t.*, a.id AS annotation_id, a.merchant, a.category, a.subcategory,
               a.tags, a.confidence, a.source, a.annotated_at, a.reasoning
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE t.id = ?
        """,
        (transaction_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    result = with_payee(conn, [dict(row)])[0]
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


class NameBody(BaseModel):
    """A name for a payment or a payee; null or blank clears it."""
    name: str | None = Field(None, max_length=MAX_NAME_LENGTH)


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
