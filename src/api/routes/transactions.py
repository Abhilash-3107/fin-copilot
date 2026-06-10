"""Transaction routes: list with filters, get by id with optional annotation."""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_db
from src.db.queries.common import parse_string_list
from src.db.queries.transactions import list_transactions

router = APIRouter()


@router.get("")
def get_transactions(
    statement_id: str | None = None,
    month: str | None = None,
    unannotated: bool = False,
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
        after=after,
        limit=limit,
    )
    if include == "annotation":
        for row in rows:
            row["tags"] = parse_string_list(row.get("tags")) if row.get("annotation_id") else []
    return rows


@router.get("/{transaction_id}")
def get_transaction(
    transaction_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    row = conn.execute(
        """
        SELECT t.*, a.id AS annotation_id, a.merchant, a.category, a.subcategory,
               a.tags, a.confidence, a.source, a.annotated_at
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
    return result
