"""Insights route: server-side aggregation for the Money Map page."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from finance_copilot.api.deps import get_db
from finance_copilot.db.queries.insights import summarize_insights

router = APIRouter()


@router.get("")
def get_insights(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM; defaults to the latest month with data"),
    account_id: str | None = Query(None, description="limit every figure to one account; default all accounts"),
    conn: sqlite3.Connection = Depends(get_db),
):
    if account_id and not conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Account not found")
    return summarize_insights(conn, month, account_id=account_id or None)
