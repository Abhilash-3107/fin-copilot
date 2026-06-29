"""Read/write helpers for runtime app settings (the app_settings key-value table).

These are settings the UI can toggle at runtime and the backend reads live — unlike
src.config.Settings, which is process-static env/.env config. The env value (e.g.
DEV_MODE) only seeds the initial default the first time a key is read.
"""
from __future__ import annotations

import sqlite3

from src.config import settings

_DEV_MODE_KEY = "dev_mode"


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value),
    )


def get_dev_mode(conn: sqlite3.Connection) -> bool:
    """Current dev_mode, falling back to the DEV_MODE env default when unset."""
    stored = _get(conn, _DEV_MODE_KEY)
    if stored is None:
        return settings.dev_mode
    return stored == "true"


def set_dev_mode(conn: sqlite3.Connection, enabled: bool) -> None:
    """Persist dev_mode. Caller commits."""
    _set(conn, _DEV_MODE_KEY, "true" if enabled else "false")
