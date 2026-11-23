"""Remembered spreadsheet layouts: which column is which, and where the file went.

Keyed by the signature of the file's header row (parsers/spreadsheet.py), so the
next file with the same header is read the same way without asking.
"""
from __future__ import annotations

import json
import sqlite3


def get_layout(conn: sqlite3.Connection, signature: str) -> dict | None:
    """{'mapping': {role: column}, 'account_id': str | None} or None. The account
    is None once the account it went to no longer exists."""
    row = conn.execute(
        "SELECT mapping, account_id FROM import_layouts WHERE signature = ?", (signature,)
    ).fetchone()
    if row is None:
        return None
    try:
        mapping = json.loads(row["mapping"])
    except ValueError:
        return None
    return {"mapping": mapping, "account_id": row["account_id"]}


def remember_layout(
    conn: sqlite3.Connection, signature: str, mapping: dict[str, int], account_id: str
) -> None:
    """Keep this reading of the header and the account the file went to, replacing
    an earlier one: the latest answer is the one the person gave last."""
    conn.execute(
        "INSERT INTO import_layouts (signature, mapping, account_id, updated_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(signature) DO UPDATE SET mapping = excluded.mapping, "
        "account_id = excluded.account_id, updated_at = excluded.updated_at",
        (signature, json.dumps(mapping, sort_keys=True), account_id),
    )
