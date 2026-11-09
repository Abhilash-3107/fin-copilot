"""CRUD helpers for people/contacts."""
from __future__ import annotations

import json
import re
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
    "self", "dad", "mom", "sister", "brother", "wife", "husband",
    "son", "daughter", "family", "friend", "roommate", "colleague", "other",
)

# The user's own other accounts: money moved to them is a Self Transfer, which
# insights exclude from spending. Only a fact the user states can make a payee
# "self"; see annotate._gate_fact_categories.
SELF_RELATIONSHIP = "self"


def is_self_relationship(relationship: str | None) -> bool:
    return bool(relationship) and relationship.strip().lower() == SELF_RELATIONSHIP


def relationship_subcategory(relationship: str | None) -> str:
    """Map a person's relationship to the Transfers subcategory for their payments."""
    if relationship and relationship.strip().lower() in _FAMILY_RELATIONSHIPS:
        return "Family"
    return "Peer Transfer"


def relationship_category(relationship: str | None) -> tuple[str, str]:
    """(category, subcategory) for payments to a person with this relationship."""
    if is_self_relationship(relationship):
        return "Self Transfers", "Self Transfer"
    return "Transfers", relationship_subcategory(relationship)


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


def person_token_matches(
    token: str, vpa: str | None, counterparty_key: str | None, desc: str
) -> bool:
    """Whether one person match-token matches a transaction.

    Single source of truth for person→transaction matching, shared by the
    annotation pipeline (stage-1 person rule) and the People page stats.

    - VPA tokens (contain '@'): exact VPA equality when the txn has a VPA, else
      substring in the description (legacy rows ingested before VPA extraction).
    - Name tokens: word-prefix match against the counterparty name segment
      ('karabi' matches 'KARABI BORA'); word-boundary in the description for
      non-UPI rows that have no counterparty_key.
    """
    if "@" in token:
        if vpa:
            return vpa == token
        return token in desc
    if counterparty_key:
        return any(word.startswith(token) for word in counterparty_key.lower().split())
    return re.search(rf"\b{re.escape(token)}", desc) is not None


def _txn_vpa(upi_meta) -> str | None:
    if not upi_meta:
        return None
    try:
        meta = json.loads(upi_meta) if isinstance(upi_meta, str) else upi_meta
        return (meta.get("vpa") or "").lower() or None
    except (json.JSONDecodeError, AttributeError):
        return None


def matched_transaction_ids(conn: sqlite3.Connection, token: str) -> list[str]:
    """Transaction ids whose counterparty matches this person's match-token.

    Reuses the pipeline's matching predicate so the People page and the auto
    annotation agree on "this is a payment to/from this person".
    """
    if not token:
        return []
    token = token.strip().lower()
    rows = conn.execute(
        "SELECT id, raw_description, counterparty_key, upi_meta FROM transactions"
    ).fetchall()
    matched = []
    for r in rows:
        desc = (r["raw_description"] or "").lower()
        if person_token_matches(token, _txn_vpa(r["upi_meta"]), r["counterparty_key"], desc):
            matched.append(r["id"])
    return matched


def people_transaction_stats(conn: sqlite3.Connection) -> dict[str, dict]:
    """Per-person match count and net sent/received over all transactions.

    Returns {person_id: {matched, sent, received, net}}. `sent` is money that
    left the account to this person (debits), `received` is money that came from
    them (credits); net = received - sent. People with no match token, or whose
    token matches nothing, get zeroes.
    """
    people = list_people(conn)
    # One scan of transactions, matched against every token, so the whole page is
    # O(people x txns) in Python but a single DB round-trip.
    rows = conn.execute(
        """
        SELECT t.id, t.raw_description, t.counterparty_key, t.upi_meta,
               t.amount, t.debit_credit
        FROM transactions t
        """
    ).fetchall()
    prepared = [
        (r, (r["raw_description"] or "").lower(), _txn_vpa(r["upi_meta"]))
        for r in rows
    ]
    stats: dict[str, dict] = {}
    for p in people:
        token = (p.get("upi") or "").strip().lower()
        acc = {"matched": 0, "sent": 0.0, "received": 0.0}
        if token:
            for r, desc, vpa in prepared:
                if person_token_matches(token, vpa, r["counterparty_key"], desc):
                    acc["matched"] += 1
                    if r["debit_credit"] == "debit":
                        acc["sent"] += r["amount"]
                    else:
                        acc["received"] += r["amount"]
        acc["sent"] = round(acc["sent"], 2)
        acc["received"] = round(acc["received"], 2)
        acc["net"] = round(acc["received"] - acc["sent"], 2)
        stats[p["id"]] = acc
    return stats
