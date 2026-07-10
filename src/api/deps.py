"""Shared FastAPI dependencies (e.g. database connection or session accessors)."""
from __future__ import annotations

import sqlite3
from collections.abc import Generator

from src.db.connection import get_connection


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Per-request connection. Migrations run once at startup (lifespan), not here."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
