"""Review policies: one user decision that answers a whole class of review
questions (migration 031).

The only kind today is 'small_personal_payment': a UPI debit of at most
max_amount to a payee that is a person (parsers/payee.py), whose own verified
labels (same direction) aren't in another category. People on the People page
never get here: the person rule decides them first. The user's answer to
"what are all these small payments to strangers?" (for most people in India:
autos and street vendors) is applied once, before any model runs.

Policy labels carry source 'policy': machine-made, re-decided whenever policies
change, and never trusted as evidence (they are a rule of thumb, not a fact).
"""
from __future__ import annotations

import sqlite3

import ulid

from finance_copilot.db.queries.categories import require_category_ids
from finance_copilot.models.annotation import AnnotationCreate

SMALL_PERSONAL_PAYMENT = "small_personal_payment"

# A policy is the user's standing answer, so its labels pass the review bar.
POLICY_CONFIDENCE = 0.9

_SELECT = """
    SELECT p.id, p.kind, p.max_amount, p.enabled, p.created_at, p.updated_at,
           p.category_id, c.name AS category, p.subcategory_id, s.name AS subcategory
    FROM review_policies p
    JOIN categories c ON c.id = p.category_id
    LEFT JOIN categories s ON s.id = p.subcategory_id
"""


def _as_dict(row: sqlite3.Row) -> dict:
    return {**dict(row), "enabled": bool(row["enabled"])}


def list_policies(conn: sqlite3.Connection, enabled_only: bool = False) -> list[dict]:
    where = " WHERE p.enabled = 1" if enabled_only else ""
    return [_as_dict(r) for r in conn.execute(_SELECT + where + " ORDER BY p.created_at, p.id")]


def get_policy(conn: sqlite3.Connection, policy_id: str) -> dict | None:
    row = conn.execute(_SELECT + " WHERE p.id = ?", (policy_id,)).fetchone()
    return _as_dict(row) if row else None


def create_policy(conn: sqlite3.Connection, kind: str, max_amount: float, category: str,
                  subcategory: str | None = None) -> dict:
    """Caller commits. Raises CategoryError for names outside the taxonomy."""
    category_id, subcategory_id = require_category_ids(conn, category, subcategory)
    policy_id = str(ulid.ULID())
    conn.execute(
        "INSERT INTO review_policies (id, kind, max_amount, category_id, subcategory_id) VALUES (?, ?, ?, ?, ?)",
        (policy_id, kind, max_amount, category_id, subcategory_id),
    )
    return get_policy(conn, policy_id)


def update_policy(conn: sqlite3.Connection, policy_id: str, changes: dict) -> dict | None:
    """Apply max_amount, category+subcategory, enabled when present. Caller commits."""
    if get_policy(conn, policy_id) is None:
        return None
    sets: list[str] = []
    params: list = []
    if "max_amount" in changes:
        sets.append("max_amount = ?")
        params.append(changes["max_amount"])
    if "category" in changes:
        sets += ["category_id = ?", "subcategory_id = ?"]
        params += require_category_ids(conn, changes["category"], changes.get("subcategory"))
    if "enabled" in changes:
        sets.append("enabled = ?")
        params.append(int(changes["enabled"]))
    if sets:
        conn.execute(
            f"UPDATE review_policies SET {', '.join(sets)}, updated_at = datetime('now') WHERE id = ?",
            [*params, policy_id],
        )
    return get_policy(conn, policy_id)


def delete_policy(conn: sqlite3.Connection, policy_id: str) -> bool:
    """Caller commits."""
    return conn.execute("DELETE FROM review_policies WHERE id = ?", (policy_id,)).rowcount > 0


def match_policy(conn: sqlite3.Connection, policies: list[dict], txn: dict, *, as_of: str | None) -> dict | None:
    """The policy that answers this transaction, or None: of the enabled
    policies passed in, the most specific (smallest max_amount) that covers it.

    as_of bounds the payee's verified history like every other history read.
    """
    from finance_copilot.db.queries.learned_rules import verified_labels
    from finance_copilot.parsers.description import is_known_shape, parse_description
    from finance_copilot.parsers.payee import PERSON, classify_payee

    if txn["debit_credit"] != "debit":
        return None
    candidates = sorted((p for p in policies if txn["amount"] <= p["max_amount"]), key=lambda p: p["max_amount"])
    if not candidates:
        return None
    parsed = parse_description(txn["raw_description"])
    if parsed.rail != "upi" or not is_known_shape(parsed) or classify_payee(parsed.counterparty) != PERSON:
        return None
    if txn["counterparty_key"]:
        labeled = {
            r["category"] for r in verified_labels(
                conn, txn["counterparty_key"], "debit", before_txn_date=as_of, exclude_transaction_id=txn["id"],
            )
        }
        candidates = [p for p in candidates if not (labeled - {p["category"]})]
    return candidates[0] if candidates else None


def annotation_for_policy(policy: dict, txn: dict) -> AnnotationCreate:
    return AnnotationCreate(
        transaction_id=txn["id"],
        category=policy["category"],
        subcategory=policy["subcategory"],
        tags=["policy"],
        confidence=POLICY_CONFIDENCE,
        source="policy",
    )
