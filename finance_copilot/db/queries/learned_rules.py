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

from finance_copilot.config import settings
from finance_copilot.db.queries.common import parse_string_list

# Only labels a human authored or vouched for may promote a merchant rule.
VERIFIED_SOURCES = ("manual", "imported")


def is_suppressed(conn: sqlite3.Connection, counterparty_key: str) -> bool:
    """Whether the user has explicitly dismissed the learned rule for this key."""
    return (
        conn.execute(
            "SELECT 1 FROM learned_rule_suppressions WHERE counterparty_key = ?",
            (counterparty_key,),
        ).fetchone()
        is not None
    )


def suppress_learned_rule(conn: sqlite3.Connection, counterparty_key: str) -> None:
    """Dismiss the learned rule for a counterparty (idempotent).

    Sticky: re-verifying the counterparty does not clear this; the user restores
    it explicitly via restore_learned_rule.
    """
    conn.execute(
        "INSERT OR IGNORE INTO learned_rule_suppressions (counterparty_key) VALUES (?)",
        (counterparty_key,),
    )


def restore_learned_rule(conn: sqlite3.Connection, counterparty_key: str) -> bool:
    """Undo a dismissal; returns True if a suppression existed and was removed."""
    cur = conn.execute(
        "DELETE FROM learned_rule_suppressions WHERE counterparty_key = ?",
        (counterparty_key,),
    )
    return cur.rowcount > 0


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
    # 'debit' / 'credit': a rule is per counterparty *and* direction. Payments to
    # a merchant and refunds from it are different questions (Health vs Refund),
    # and mixing them would both break purity and mislabel refunds.
    direction: str | None = None


def _representative(rows: list[dict], category: str) -> dict:
    """Pick the annotation whose subcategory/merchant/tags represent the category.

    Most recent verified label of the winning category — newer edits reflect the
    user's current intent for that merchant.
    """
    of_cat = [r for r in rows if r["category"] == category]
    return max(of_cat, key=lambda r: r["annotated_at"] or "")


def _promote(counterparty_key: str, rows: list[dict], direction: str | None = None) -> LearnedRule | None:
    """Apply the support+purity promotion bar to one counterparty's verified rows.

    The single source of truth for "is this an established rule": both the
    per-key lookup and the bulk listing route through here so they can never
    disagree. Callers pass only verified, causally-filtered rows.
    """
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
        direction=direction,
    )


def lookup_learned_rule(
    conn: sqlite3.Connection,
    counterparty_key: str | None,
    *,
    before_txn_date: str | None = None,
    exclude_transaction_id: str | None = None,
    direction: str | None = None,
) -> LearnedRule | None:
    """Return the established merchant rule for a counterparty, or None.

    Established := >= learned_rule_min_support verified labels for the modal
    category AND purity >= learned_rule_purity. `before_txn_date` /
    `exclude_transaction_id` enforce causality (a replayed transaction only sees
    labels that existed before it), mirroring the counterparty prior.

    Same-day asymmetry (deliberate, documented so it isn't chased as a bug):
    the causal cutoff is strict (`t.txn_date < before_txn_date`), so in the eval
    harness a transaction never sees another same-day label - dates are
    day-granular and intra-day order is unknown, so counting a same-day sibling
    would leak. Production (list_learned_rules and the live annotate path) passes
    no before_txn_date and therefore sees every label including same-day ones.
    So eval support for a merchant can be one lower than prod on a day the user
    labeled two of its transactions; "eval says not-established, prod fires" on
    that boundary is expected, not a divergence to fix.
    """
    if not counterparty_key:
        return None

    # A user dismissal overrides the computed rule entirely — no firing, no display.
    if is_suppressed(conn, counterparty_key):
        return None

    placeholders = ",".join("?" * len(VERIFIED_SOURCES))
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
              AND (? IS NULL OR t.debit_credit = ?)
            """,
            (
                counterparty_key,
                *VERIFIED_SOURCES,
                before_txn_date, before_txn_date,
                exclude_transaction_id, exclude_transaction_id,
                direction, direction,
            ),
        ).fetchall()
    ]

    return _promote(counterparty_key, rows, direction)


def list_learned_rules(conn: sqlite3.Connection) -> list[LearnedRule]:
    """All currently-established merchant rules (for the transparency endpoint).

    Computed live from present-day annotations (no causal cutoff) so the user
    sees what the pipeline would apply right now. Two queries total (verified
    rows, then the suppression set); grouping and promotion happen in Python via
    the same _promote() the per-key lookup uses, so the list can never diverge
    from what the pipeline would actually fire.
    """
    placeholders = ",".join("?" * len(VERIFIED_SOURCES))
    rows = conn.execute(
        f"""
        SELECT t.counterparty_key AS counterparty_key, t.debit_credit,
               a.category, a.subcategory, a.merchant, a.tags, a.annotated_at
        FROM annotations a
        JOIN transactions t ON t.id = a.transaction_id
        WHERE t.counterparty_key IS NOT NULL
          AND a.category IS NOT NULL
          AND a.source IN ({placeholders})
        """,
        VERIFIED_SOURCES,
    ).fetchall()

    suppressed = {
        r["counterparty_key"]
        for r in conn.execute("SELECT counterparty_key FROM learned_rule_suppressions").fetchall()
    }

    by_key: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        key = r["counterparty_key"]
        if key in suppressed:
            continue
        by_key.setdefault((key, r["debit_credit"]), []).append(dict(r))

    out = [
        rule
        for (key, direction), key_rows in by_key.items()
        if (rule := _promote(key, key_rows, direction)) is not None
    ]
    out.sort(key=lambda r: (-r.support, r.counterparty_key, r.direction or ""))
    return out
