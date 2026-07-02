"""Learned merchant memory — a deterministic annotation stage derived from the
user's own verified labels.

When a counterparty (by indexed transactions.counterparty_key) has accumulated
enough *human-verified* annotations that agree on a category, we can label future
transactions to it deterministically, with no embedding call and no LLM — the
recurring, single-purpose bulk of a mature user's statement (SWIGGY, LICIOUS,
OBEROIFC, ...). This is the exact-match/cache idea from the June review, upgraded
with a support+purity promotion bar.

Design (on-demand, not materialized):
  - Rules are *computed* from the annotations table on lookup, not stored. The
    counterparty_key index (migration 017) makes this an indexed lookup, the same
    shape the counterparty prior already pays for. Benefits: single source of
    truth (no staleness), automatic demotion (a correction lowers purity on the
    next lookup), and — critically — the eval harness and production share one
    code path because `before_txn_date` makes the computation causal.
  - Promotion is gated on *human-verified* labels only (manual/imported). Machine
    sources (rule/rag_*/llm/learned_rule) are excluded so a recurring machine
    mislabel can never bootstrap itself into a deterministic rule (the same
    anti-feedback-loop principle as annotate._TRUSTED_SOURCES).
  - Personal counterparties are handled by the stage-1 person rule and never
    reach here in practice; the purity bar also blocks the mixed-purpose names
    (cab drivers, split-expense contacts) that the person rule doesn't catch.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass

from src.config import settings
from src.db.queries.common import parse_string_list

# Only labels a human authored or vouched for may promote a merchant rule.
_VERIFIED_SOURCES = ("manual", "imported")


@dataclass
class LearnedRule:
    counterparty_key: str
    category: str
    subcategory: str | None
    merchant: str | None
    tags: list[str]
    support: int          # verified labels agreeing on `category`
    total: int            # verified labels for this counterparty (any category)
    purity: float         # support / total


def _representative(rows: list[dict], category: str) -> dict:
    """Pick the annotation whose subcategory/merchant/tags represent the category.

    Most recent verified label of the winning category — newer edits reflect the
    user's current intent for that merchant.
    """
    of_cat = [r for r in rows if r["category"] == category]
    return max(of_cat, key=lambda r: r["annotated_at"] or "")


def lookup_learned_rule(
    conn: sqlite3.Connection,
    counterparty_key: str | None,
    *,
    before_txn_date: str | None = None,
    exclude_transaction_id: str | None = None,
) -> LearnedRule | None:
    """Return the established merchant rule for a counterparty, or None.

    Established := >= learned_rule_min_support verified labels for the modal
    category AND purity >= learned_rule_purity. `before_txn_date` /
    `exclude_transaction_id` enforce causality (a replayed transaction only sees
    labels that existed before it), mirroring the counterparty prior.
    """
    if not counterparty_key:
        return None

    placeholders = ",".join("?" * len(_VERIFIED_SOURCES))
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT a.category, a.subcategory, a.merchant, a.tags, a.annotated_at
            FROM annotations a
            JOIN transactions t ON t.id = a.transaction_id
            WHERE t.counterparty_key = ?
              AND a.category IS NOT NULL
              AND a.source IN ({placeholders})
              AND (? IS NULL OR t.txn_date < ?)
              AND (? IS NULL OR t.id != ?)
            """,
            (
                counterparty_key,
                *_VERIFIED_SOURCES,
                before_txn_date, before_txn_date,
                exclude_transaction_id, exclude_transaction_id,
            ),
        ).fetchall()
    ]

    total = len(rows)
    if total == 0:
        return None

    counts = Counter(r["category"] for r in rows)
    category, support = counts.most_common(1)[0]
    purity = support / total

    if support < settings.learned_rule_min_support or purity < settings.learned_rule_purity:
        return None

    rep = _representative(rows, category)
    return LearnedRule(
        counterparty_key=counterparty_key,
        category=category,
        subcategory=rep.get("subcategory"),
        merchant=rep.get("merchant"),
        tags=parse_string_list(rep.get("tags")),
        support=support,
        total=total,
        purity=round(purity, 4),
    )


def list_learned_rules(conn: sqlite3.Connection) -> list[LearnedRule]:
    """All currently-established merchant rules (for the transparency endpoint).

    Computed live from present-day annotations (no causal cutoff) so the user
    sees what the pipeline would apply right now.
    """
    keys = [
        r["counterparty_key"]
        for r in conn.execute(
            "SELECT DISTINCT counterparty_key FROM transactions WHERE counterparty_key IS NOT NULL"
        ).fetchall()
    ]
    out: list[LearnedRule] = []
    for key in keys:
        rule = lookup_learned_rule(conn, key)
        if rule is not None:
            out.append(rule)
    out.sort(key=lambda r: (-r.support, r.counterparty_key))
    return out
