"""CRUD helpers for people/contacts."""
from __future__ import annotations

import sqlite3

import ulid


def create_person(conn: sqlite3.Connection, name: str, upi: str | None = None) -> dict:
    person_id = str(ulid.ULID())
    conn.execute(
        "INSERT INTO people (id, name, upi) VALUES (?, ?, ?)",
        (person_id, name, upi),
    )
    return {"id": person_id, "name": name, "upi": upi}


def get_person(conn: sqlite3.Connection, person_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    return dict(row) if row else None


def list_people(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM people ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def search_people(conn: sqlite3.Connection, query: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM people WHERE name LIKE ? OR upi LIKE ? ORDER BY name LIMIT 20",
        (f"%{query}%", f"%{query}%"),
    ).fetchall()
    return [dict(r) for r in rows]


def update_person(conn: sqlite3.Connection, person_id: str, name: str, upi: str | None) -> dict | None:
    conn.execute(
        "UPDATE people SET name = ?, upi = ? WHERE id = ?",
        (name, upi, person_id),
    )
    return get_person(conn, person_id)


def delete_person(conn: sqlite3.Connection, person_id: str) -> bool:
    cursor = conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
    return cursor.rowcount > 0
