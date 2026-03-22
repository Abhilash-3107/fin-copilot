"""Shared FastAPI dependencies (e.g. database connection or session accessors)."""
from __future__ import annotations

import sqlite3
from typing import Generator

from src.db.connection import get_db as db_get_db


def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = db_get_db()
    try:
        yield conn
    finally:
        conn.close()
