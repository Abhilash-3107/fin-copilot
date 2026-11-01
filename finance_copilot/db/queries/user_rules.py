"""User-authored categorization rules: CRUD, matching, preview, and apply.

Matching reuses the built-in rule engine's word-bounded matcher over the same
fields (description + UPI note), so a user rule behaves exactly like the
shipped MERCHANT_RULES. When several rules match one transaction the longest
pattern wins (the most specific phrase), then the oldest rule.
"""
from __future__ import annotations

import sqlite3

import ulid

from finance_copilot.models.annotation import AnnotationCreate
from finance_copilot.pipeline.rules import _extract_upi_note, _pattern_matches

# A user rule is deliberate, so it auto-accepts like the built-in rules do.
USER_RULE_CONFIDENCE = 0.95

# Labels a rule must never overwrite when applied to existing transactions:
# a human chose them.
_PROTECTED_SOURCES = ("manual", "imported")

_RULE_SELECT = """
    SELECT r.id, r.pattern, r.merchant, r.enabled, r.created_at, r.updated_at,
           r.category_id, c.name AS category,
           r.subcategory_id, s.name AS subcategory
    FROM user_rules r
    JOIN categories c ON c.id = r.category_id
    LEFT JOIN categories s ON s.id = r.subcategory_id
"""


class UserRuleError(ValueError):
    """A rule the taxonomy can't accept (unknown category, empty pattern)."""


def _clean_pattern(pattern: str) -> str:
    cleaned = " ".join(pattern.split()).lower()
    if not cleaned:
        raise UserRuleError("A rule needs a word or phrase to match")
    return cleaned


def _resolve(conn: sqlite3.Connection, category: str, subcategory: str | None) -> tuple[str, str | None]:
    from finance_copilot.db.queries.categories import resolve_category_ids

    category_id, subcategory_id = resolve_category_ids(conn, category, subcategory)
    if category_id is None:
        raise UserRuleError(f"Unknown category: {category}")
    if subcategory and subcategory_id is None:
        raise UserRuleError(f"Unknown subcategory under {category}: {subcategory}")
    return category_id, subcategory_id


def list_user_rules(conn: sqlite3.Connection, enabled_only: bool = False) -> list[dict]:
    where = " WHERE r.enabled = 1" if enabled_only else ""
    rows = conn.execute(_RULE_SELECT + where + " ORDER BY r.created_at, r.id").fetchall()
    return [_as_dict(r) for r in rows]


def get_user_rule(conn: sqlite3.Connection, rule_id: str) -> dict | None:
    row = conn.execute(_RULE_SELECT + " WHERE r.id = ?", (rule_id,)).fetchone()
    return _as_dict(row) if row else None


def _as_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    return d


def create_user_rule(
    conn: sqlite3.Connection,
    pattern: str,
    category: str,
    subcategory: str | None = None,
    merchant: str | None = None,
) -> dict:
    """Caller commits."""
    pattern = _clean_pattern(pattern)
    category_id, subcategory_id = _resolve(conn, category, subcategory)
    rule_id = str(ulid.ULID())
    conn.execute(
        "INSERT INTO user_rules (id, pattern, category_id, subcategory_id, merchant) VALUES (?, ?, ?, ?, ?)",
        (rule_id, pattern, category_id, subcategory_id, (merchant or "").strip() or None),
    )
    return get_user_rule(conn, rule_id)


def update_user_rule(conn: sqlite3.Connection, rule_id: str, changes: dict) -> dict | None:
    """Apply the keys present in `changes` (pattern, category+subcategory,
    merchant, enabled). Caller commits."""
    current = get_user_rule(conn, rule_id)
    if current is None:
        return None
    sets: list[str] = []
    params: list = []
    if "pattern" in changes:
        sets.append("pattern = ?")
        params.append(_clean_pattern(changes["pattern"]))
    if "category" in changes:
        category_id, subcategory_id = _resolve(conn, changes["category"], changes.get("subcategory"))
        sets += ["category_id = ?", "subcategory_id = ?"]
        params += [category_id, subcategory_id]
    if "merchant" in changes:
        sets.append("merchant = ?")
        params.append((changes["merchant"] or "").strip() or None)
    if "enabled" in changes:
        sets.append("enabled = ?")
        params.append(1 if changes["enabled"] else 0)
    if sets:
        conn.execute(
            f"UPDATE user_rules SET {', '.join(sets)}, updated_at = datetime('now') WHERE id = ?",
            [*params, rule_id],
        )
    return get_user_rule(conn, rule_id)


def delete_user_rule(conn: sqlite3.Connection, rule_id: str) -> bool:
    """Caller commits. Annotations it already produced keep their label."""
    return conn.execute("DELETE FROM user_rules WHERE id = ?", (rule_id,)).rowcount > 0


def _haystack(txn: dict) -> str:
    return f"{(txn.get('raw_description') or '').lower()} {_extract_upi_note(txn).lower()}"


def match_user_rule(rules: list[dict], txn: dict) -> dict | None:
    """The most specific enabled rule matching `txn`, or None. `rules` comes from
    list_user_rules (created order), so ties on length go to the oldest."""
    haystack = _haystack(txn)
    best: dict | None = None
    for rule in rules:
        if not rule["enabled"] or not _pattern_matches(rule["pattern"], haystack):
            continue
        if best is None or len(rule["pattern"]) > len(best["pattern"]):
            best = rule
    return best


def annotation_for(rule: dict, txn: dict) -> AnnotationCreate:
    return AnnotationCreate(
        transaction_id=txn["id"],
        merchant=rule.get("merchant"),
        category=rule["category"],
        subcategory=rule.get("subcategory"),
        confidence=USER_RULE_CONFIDENCE,
        source="user_rule",
    )


def _matching_rows(conn: sqlite3.Connection, pattern: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.id, t.txn_date, t.amount, t.debit_credit, t.raw_description, t.upi_meta,
               a.id AS annotation_id, a.category, a.subcategory, a.source
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        ORDER BY t.txn_date DESC, t.id
        """
    ).fetchall()
    return [dict(r) for r in rows if _pattern_matches(pattern, _haystack(dict(r)))]


def preview_rule(
    conn: sqlite3.Connection,
    pattern: str,
    category: str,
    subcategory: str | None = None,
    sample_size: int = 5,
) -> dict:
    """What applying this rule to existing transactions would do.

    matches     every transaction the pattern hits
    relabel     auto-labelled with a different category, would change
    unlabeled   not categorized yet, would get this label
    unchanged   already carry this exact label
    protected   labelled by a human (manual/imported), left alone
    """
    pattern = _clean_pattern(pattern)
    _resolve(conn, category, subcategory)
    rows = _matching_rows(conn, pattern)
    counts = {"matches": len(rows), "relabel": 0, "unlabeled": 0, "unchanged": 0, "protected": 0}
    for r in rows:
        counts[_bucket(r, category, subcategory)] += 1
    sample = [
        {k: r[k] for k in ("id", "txn_date", "amount", "debit_credit", "raw_description", "category", "subcategory")}
        for r in rows[:sample_size]
    ]
    return {**counts, "sample": sample}


def _bucket(row: dict, category: str, subcategory: str | None) -> str:
    if row["annotation_id"] is None:
        return "unlabeled"
    if row["source"] in _PROTECTED_SOURCES:
        return "protected"
    if row["category"] == category and (row["subcategory"] or None) == (subcategory or None):
        return "unchanged"
    return "relabel"


def apply_rule_to_existing(conn: sqlite3.Connection, rule: dict) -> dict:
    """Label every matching transaction that a human hasn't labelled. Caller
    commits. Returns counts plus the touched transaction ids (for re-embedding).

    The user reviewed the preview and chose to apply, so like bulk relabel the
    touched rows become manual labels (original_source keeps the machine
    source). Only future auto-annotate runs produce source 'user_rule'.
    """
    from finance_copilot.db.queries.annotations import insert_annotation, update_annotation
    from finance_copilot.db.queries.feedback_stats import record_feedback
    from finance_copilot.models.annotation import Annotation
    from finance_copilot.pipeline.sources import MODEL_SOURCES

    touched: list[str] = []
    counts = {"relabeled": 0, "labeled": 0, "protected": 0, "unchanged": 0}
    for row in _matching_rows(conn, rule["pattern"]):
        bucket = _bucket(row, rule["category"], rule.get("subcategory"))
        if bucket == "protected":
            counts["protected"] += 1
        elif bucket == "unchanged":
            counts["unchanged"] += 1
        elif bucket == "unlabeled":
            insert_annotation(conn, Annotation(
                transaction_id=row["id"],
                merchant=rule.get("merchant"),
                category=rule["category"],
                subcategory=rule.get("subcategory"),
                confidence=1.0,
                source="manual",
            ))
            counts["labeled"] += 1
            touched.append(row["id"])
        else:
            # The user overruling a machine label by rule is a correction, the
            # same signal bulk relabel records.
            if row["source"] in MODEL_SOURCES:
                record_feedback(conn, row["source"], row["category"], "corrected")
            patch = {
                "category": rule["category"],
                "subcategory": rule.get("subcategory"),
                "category_id": rule["category_id"],
                "subcategory_id": rule.get("subcategory_id"),
                "confidence": 1.0,
            }
            if rule.get("merchant"):
                patch["merchant"] = rule["merchant"]
            update_annotation(conn, row["annotation_id"], patch)
            counts["relabeled"] += 1
            touched.append(row["id"])
    return {**counts, "transaction_ids": touched}
