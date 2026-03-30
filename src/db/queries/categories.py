"""Read helpers for the categories table."""
from __future__ import annotations

import sqlite3


def get_category_tree(conn: sqlite3.Connection) -> list[dict]:
    """Return all categories as flat list of dicts with id, name, parent_id."""
    rows = conn.execute(
        "SELECT id, name, parent_id FROM categories ORDER BY parent_id NULLS FIRST, name"
    ).fetchall()
    return [dict(row) for row in rows]


def get_category_names_flat(conn: sqlite3.Connection) -> list[str]:
    """Return category strings in 'Category > Subcategory' format for LLM prompts.

    Top-level categories appear as just 'Category'.
    """
    rows = get_category_tree(conn)
    by_id = {r["id"]: r for r in rows}
    result: list[str] = []
    for row in rows:
        if row["parent_id"] is None:
            result.append(row["name"])
        else:
            parent_name = by_id[row["parent_id"]]["name"]
            result.append(f"{parent_name} > {row['name']}")
    return result
