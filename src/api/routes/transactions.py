"""Transaction routes: list with filters, get by id with optional annotation."""
from __future__ import annotations

import csv
import io
import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.deps import get_db
from src.db.queries.app_settings import get_dev_mode
from src.db.queries.common import parse_string_list
from src.db.queries.transactions import list_transaction_facets, list_transactions

router = APIRouter()


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    parts = [p for p in value.split(",") if p]
    return parts or None


@router.get("")
def get_transactions(
    statement_id: str | None = None,
    month: str | None = None,
    unannotated: bool = False,
    q: str | None = Query(None, description="substring match on description, merchant, UPI note"),
    category: str | None = Query(None, description="comma-separated annotation categories"),
    source: str | None = Query(None, description="comma-separated annotation sources"),
    merchant: str | None = Query(None, description="exact annotated merchant"),
    person: str | None = Query(None, description="match-token of a person; filters to their matched transactions"),
    include: str | None = Query(None, description="'annotation' joins each row's annotation"),
    after: str | None = Query(None, description="cursor: id of the last row of the previous page"),
    limit: int | None = Query(None, ge=1, le=1000),
    conn: sqlite3.Connection = Depends(get_db),
):
    rows = list_transactions(
        conn,
        statement_id=statement_id,
        month=month,
        unannotated=unannotated,
        include_annotation=include == "annotation",
        q=q,
        categories=_split_csv(category),
        sources=_split_csv(source),
        merchant=merchant,
        person_token=person,
        after=after,
        limit=limit,
    )
    if include == "annotation":
        for row in rows:
            row["tags"] = parse_string_list(row.get("tags")) if row.get("annotation_id") else []
    return rows


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
    "confidence", "source",
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
    rows = list_transactions(conn, include_annotation=True)
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
        raise HTTPException(status_code=404, detail="Transaction not found")
    result = dict(row)
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
