"""Transaction routes: list with filters, get by id with optional annotation."""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from financebot.api.deps import get_db
from financebot.db.queries.transactions import list_transactions

router = APIRouter()


@router.get("")
def get_transactions(
    statement_id: str | None = None,
    month: str | None = None,
    unannotated: bool = False,
    conn: sqlite3.Connection = Depends(get_db),
):
    return list_transactions(conn, statement_id=statement_id, month=month, unannotated=unannotated)


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
    return result
