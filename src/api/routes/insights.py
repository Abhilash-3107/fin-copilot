"""Insights route: server-side aggregation for the Money Map page."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_db
from src.db.queries.insights import summarize_insights

router = APIRouter()


@router.get("")
def get_insights(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM; defaults to the latest month with data"),
    conn: sqlite3.Connection = Depends(get_db),
):
    return summarize_insights(conn, month)
