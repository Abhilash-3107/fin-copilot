"""CRUD helpers for people/contacts."""
from __future__ import annotations

import sqlite3

import ulid

# Relationships that mean "family" — payments to these people are labelled
# Transfers › Family rather than the generic Transfers › Peer Transfer. Kept as a
# flat set (not a per-relationship subcategory map) because Family is the only
# subcategory that currently distinguishes the person rule; everything else,
# including an unlabelled person, is a Peer Transfer.
_FAMILY_RELATIONSHIPS = frozenset(
    {
        "dad", "mom", "father", "mother", "parent", "sister", "brother",
        "sibling", "wife", "husband", "spouse", "son", "daughter", "family",
    }
)

# Ordered choices for the People UI dropdown. Free-text is still accepted at the
# DB layer; this is just the curated set the picker offers.
RELATIONSHIP_CHOICES = (
    "dad", "mom", "sister", "brother", "wife", "husband",
    "son", "daughter", "family", "friend", "roommate", "colleague", "other",
)


def relationship_subcategory(relationship: str | None) -> str:
    """Map a person's relationship to the Transfers subcategory for their payments."""
    if relationship and relationship.strip().lower() in _FAMILY_RELATIONSHIPS:
        return "Family"
    return "Peer Transfer"


def create_person(
    conn: sqlite3.Connection,
    name: str,
    upi: str | None = None,
    relationship: str | None = None,
) -> dict:
    person_id = str(ulid.ULID())
    conn.execute(
        "INSERT INTO people (id, name, upi, relationship) VALUES (?, ?, ?, ?)",
        (person_id, name, upi, relationship),
    )
    return {"id": person_id, "name": name, "upi": upi, "relationship": relationship}


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


def update_person(
    conn: sqlite3.Connection,
    person_id: str,
    name: str,
    upi: str | None,
    relationship: str | None = None,
) -> dict | None:
    conn.execute(
        "UPDATE people SET name = ?, upi = ?, relationship = ? WHERE id = ?",
        (name, upi, relationship, person_id),
    )
    return get_person(conn, person_id)


def delete_person(conn: sqlite3.Connection, person_id: str) -> bool:
    cursor = conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
    return cursor.rowcount > 0
