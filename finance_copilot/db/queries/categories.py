"""Read helpers for the categories table."""
from __future__ import annotations

import sqlite3

import ulid


class CategoryError(ValueError):
    """A category mutation the caller should surface as a 4xx (bad request)."""


def get_category_tree(conn: sqlite3.Connection) -> list[dict]:
    """Return all categories as flat list of dicts with id, name, parent_id."""
    rows = conn.execute(
        "SELECT id, name, parent_id FROM categories ORDER BY parent_id NULLS FIRST, name"
    ).fetchall()
    return [dict(row) for row in rows]


def resolve_category_ids(
    conn: sqlite3.Connection,
    category: str | None,
    subcategory: str | None,
) -> tuple[str | None, str | None]:
    """Map (category name, subcategory name) to (category_id, subcategory_id).

    Returns None for whichever name doesn't exist in the taxonomy (subcategory
    must belong to the resolved category).
    """
    category_id = None
    if category:
        row = conn.execute(
            "SELECT id FROM categories WHERE name = ? AND parent_id IS NULL", (category,)
        ).fetchone()
        category_id = row["id"] if row else None

    subcategory_id = None
    if subcategory and category_id:
        row = conn.execute(
            "SELECT id FROM categories WHERE name = ? AND parent_id = ?",
            (subcategory, category_id),
        ).fetchone()
        subcategory_id = row["id"] if row else None

    return category_id, subcategory_id


def require_category_ids(conn: sqlite3.Connection, category: str, subcategory: str | None) -> tuple[str, str | None]:
    """resolve_category_ids for names a user chose: unknown names raise CategoryError."""
    category_id, subcategory_id = resolve_category_ids(conn, category, subcategory)
    if category_id is None:
        raise CategoryError(f"Unknown category: {category}")
    if subcategory and subcategory_id is None:
        raise CategoryError(f"Unknown subcategory under {category}: {subcategory}")
    return category_id, subcategory_id


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


# --- Category management (4.13): add / rename / merge subcategories. -----------
# Annotations store both the id (category_id/subcategory_id) and the denormalized
# name (category/subcategory); every mutation keeps the two in lockstep so the
# rest of the app - which reads the names - sees the change immediately.

def _get_category(conn: sqlite3.Connection, category_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, name, parent_id FROM categories WHERE id = ?", (category_id,)
    ).fetchone()
    return dict(row) if row else None


def add_subcategory(conn: sqlite3.Connection, parent_id: str, name: str) -> dict:
    """Create a subcategory under a top-level category. Caller commits."""
    name = name.strip()
    if not name:
        raise CategoryError("Subcategory name can't be empty")
    parent = _get_category(conn, parent_id)
    if parent is None or parent["parent_id"] is not None:
        raise CategoryError("Parent must be an existing top-level category")
    dup = conn.execute(
        "SELECT 1 FROM categories WHERE parent_id = ? AND name = ? COLLATE NOCASE",
        (parent_id, name),
    ).fetchone()
    if dup:
        raise CategoryError(f"'{name}' already exists under {parent['name']}")
    new_id = str(ulid.ULID())
    conn.execute(
        "INSERT INTO categories (id, name, parent_id) VALUES (?, ?, ?)",
        (new_id, name, parent_id),
    )
    return {"id": new_id, "name": name, "parent_id": parent_id}


def rename_subcategory(conn: sqlite3.Connection, category_id: str, name: str) -> dict:
    """Rename a subcategory and cascade the new name onto its annotations. Caller commits."""
    name = name.strip()
    if not name:
        raise CategoryError("Subcategory name can't be empty")
    cat = _get_category(conn, category_id)
    if cat is None or cat["parent_id"] is None:
        raise CategoryError("Only subcategories can be renamed here")
    dup = conn.execute(
        "SELECT 1 FROM categories WHERE parent_id = ? AND name = ? COLLATE NOCASE AND id != ?",
        (cat["parent_id"], name, category_id),
    ).fetchone()
    if dup:
        raise CategoryError(f"'{name}' already exists in this category")
    conn.execute("UPDATE categories SET name = ? WHERE id = ?", (name, category_id))
    # Keep the denormalized annotation name in sync with the id.
    conn.execute(
        "UPDATE annotations SET subcategory = ? WHERE subcategory_id = ?",
        (name, category_id),
    )
    return {"id": category_id, "name": name, "parent_id": cat["parent_id"]}


def merge_subcategory(conn: sqlite3.Connection, source_id: str, target_id: str) -> dict:
    """Merge source subcategory into target: repoint every annotation and user
    rule from source to target, then delete source. Both must be subcategories of the same parent.
    Caller commits."""
    if source_id == target_id:
        raise CategoryError("Can't merge a subcategory into itself")
    src = _get_category(conn, source_id)
    tgt = _get_category(conn, target_id)
    if src is None or tgt is None or src["parent_id"] is None or tgt["parent_id"] is None:
        raise CategoryError("Both must be existing subcategories")
    if src["parent_id"] != tgt["parent_id"]:
        raise CategoryError("Can only merge subcategories within the same category")
    moved = conn.execute(
        "UPDATE annotations SET subcategory = ?, subcategory_id = ? WHERE subcategory_id = ?",
        (tgt["name"], target_id, source_id),
    ).rowcount
    # User rules point at subcategories by id; follow the merge like annotations.
    conn.execute(
        "UPDATE user_rules SET subcategory_id = ? WHERE subcategory_id = ?", (target_id, source_id)
    )
    conn.execute(
        "UPDATE review_policies SET subcategory_id = ? WHERE subcategory_id = ?", (target_id, source_id)
    )
    conn.execute("DELETE FROM categories WHERE id = ?", (source_id,))
    return {"merged_into": target_id, "moved": moved}
