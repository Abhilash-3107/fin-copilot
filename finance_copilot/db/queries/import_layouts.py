"""Remembered spreadsheet layouts: which column is which, and where the file went.

Keyed by the signature of the file's header row (parsers/spreadsheet.py), so the
next file with the same header is read the same way without asking.
"""
from __future__ import annotations

import json
import sqlite3


def get_layout(conn: sqlite3.Connection, signature: str) -> dict | None:
    """{'mapping': {role: column}, 'account_id': str | None, 'date_order': 'dmy' | 'mdy' | None}
    or None. The account is None once the account it went to no longer exists; the
    date order is the person's answer for a date column that did not say."""
    row = conn.execute(
        "SELECT mapping, account_id, date_order FROM import_layouts WHERE signature = ?", (signature,)
    ).fetchone()
    if row is None:
        return None
    try:
        mapping = json.loads(row["mapping"])
    except ValueError:
        return None
    return {"mapping": mapping, "account_id": row["account_id"], "date_order": row["date_order"]}


def remember_layout(
    conn: sqlite3.Connection, signature: str, mapping: dict[str, int], account_id: str | None,
    date_order: str | None = None,
) -> None:
    """Keep this reading of the header and the account the file went to (None for a
    file with no header row: its account is always asked), replacing an earlier one:
    the latest answer is the one the person gave last. The date order is kept only
    when the person answered it; a file that said it itself leaves the last answer."""
    conn.execute(
        "INSERT INTO import_layouts (signature, mapping, account_id, date_order, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(signature) DO UPDATE SET mapping = excluded.mapping, "
        "account_id = excluded.account_id, "
        "date_order = COALESCE(excluded.date_order, import_layouts.date_order), "
        "updated_at = excluded.updated_at",
        (signature, json.dumps(mapping, sort_keys=True), account_id, date_order),
    )
