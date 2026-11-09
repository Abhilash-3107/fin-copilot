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
from finance_copilot.pipeline.sources import VERIFIED_SOURCES

_SOURCES = sorted(VERIFIED_SOURCES)


def is_suppressed(conn: sqlite3.Connection, counterparty_key: str, direction: str) -> bool:
    """Whether the user dismissed the learned rule for this payee and direction."""
    return conn.execute(
        "SELECT 1 FROM learned_rule_suppressions WHERE counterparty_key = ? AND direction = ?",
        (counterparty_key, direction),
    ).fetchone() is not None


def suppress_learned_rule(conn: sqlite3.Connection, counterparty_key: str, direction: str) -> None:
    """Dismiss one learned rule (idempotent). Sticky: re-verifying the payee does
    not clear it; restore_learned_rule does."""
    conn.execute(
        "INSERT OR IGNORE INTO learned_rule_suppressions (counterparty_key, direction) VALUES (?, ?)",
        (counterparty_key, direction),
    )


def restore_learned_rule(conn: sqlite3.Connection, counterparty_key: str, direction: str) -> bool:
    """Undo a dismissal; True if one existed."""
    return conn.execute(
        "DELETE FROM learned_rule_suppressions WHERE counterparty_key = ? AND direction = ?",
        (counterparty_key, direction),
    ).rowcount > 0


@dataclass(frozen=True)
class LabelStats:
    """How consistently the user labels one payee in one direction."""
    category: str   # modal category
    support: int    # verified labels in the modal category
    total: int      # verified labels in any category
    purity: float   # support / total


def label_stats(rows: list[dict]) -> LabelStats | None:
    """Stats over verified label rows ({"category": ...}); None when there are none.
    The one definition of "how consistent is the user about this payee", shared
    by learned-rule promotion and the RAG conflict guard."""
    if not rows:
        return None
    category, support = Counter(r["category"] for r in rows).most_common(1)[0]
    return LabelStats(category, support, len(rows), support / len(rows))


def verified_labels(
    conn: sqlite3.Connection,
    counterparty_key: str,
    direction: str,
    *,
    before_txn_date: str,
    exclude_transaction_id: str | None = None,
) -> list[dict]:
    """Human-verified labels for one payee and direction, dated strictly before
    before_txn_date (causal: a replayed transaction sees only earlier labels, so
    production and the eval harness behave the same).

    Day granularity: same-day labels are invisible, since intra-day order is
    unknown and counting a same-day sibling would leak in the eval.
    """
    return [
        dict(r) for r in conn.execute(
            f"""
            SELECT a.category, a.subcategory, a.merchant, a.tags, a.annotated_at
            FROM annotations a
            JOIN transactions t ON t.id = a.transaction_id
            WHERE t.counterparty_key = ? AND t.debit_credit = ? AND t.txn_date < ?
              AND a.source IN ({",".join("?" * len(_SOURCES))})
              AND t.id IS NOT ?
            """,
            (counterparty_key, direction, before_txn_date, *_SOURCES, exclude_transaction_id),
        )
    ]


@dataclass
class LearnedRule:
    counterparty_key: str
    # 'debit' / 'credit': a rule is per payee *and* direction. Payments to a
    # merchant and refunds from it are different questions (Health vs Refund).
    direction: str
    category: str
    subcategory: str | None
    merchant: str | None
    tags: list[str]
    support: int          # verified labels agreeing on `category`
    total: int            # verified labels for this payee and direction
    purity: float         # support / total


def _promote(counterparty_key: str, direction: str, rows: list[dict]) -> LearnedRule | None:
    """Apply the support+purity promotion bar to one payee's verified rows.

    The single source of truth for "is this an established rule": the per-key
    lookup and the bulk listing both route through here.
    """
    stats = label_stats(rows)
    if stats is None or stats.support < settings.learned_rule_min_support or stats.purity < settings.learned_rule_purity:
        return None
    # The most recent verified label of the winning category carries the
    # user's current subcategory/merchant/tags for that payee.
    rep = max((r for r in rows if r["category"] == stats.category), key=lambda r: r["annotated_at"] or "")
    return LearnedRule(
        counterparty_key=counterparty_key,
        direction=direction,
        category=stats.category,
        subcategory=rep["subcategory"],
        merchant=rep["merchant"],
        tags=parse_string_list(rep["tags"]),
        support=stats.support,
        total=stats.total,
        purity=round(stats.purity, 4),
    )


def lookup_learned_rule(
    conn: sqlite3.Connection,
    counterparty_key: str,
    direction: str,
    *,
    before_txn_date: str,
    exclude_transaction_id: str | None = None,
) -> LearnedRule | None:
    """The established rule for a payee and direction, or None.

    Established := >= learned_rule_min_support verified labels for the modal
    category AND purity >= learned_rule_purity, over labels dated before
    before_txn_date (see verified_labels). A user dismissal overrides it.
    """
    if is_suppressed(conn, counterparty_key, direction):
        return None
    rows = verified_labels(
        conn, counterparty_key, direction,
        before_txn_date=before_txn_date, exclude_transaction_id=exclude_transaction_id,
    )
    return _promote(counterparty_key, direction, rows)


def list_learned_rules(conn: sqlite3.Connection) -> list[LearnedRule]:
    """All currently established rules (for the transparency endpoint), over
    every label that exists today, grouped by payee and direction and promoted
    by the same _promote the per-key lookup uses."""
    rows = conn.execute(
        f"""
        SELECT t.counterparty_key, t.debit_credit,
               a.category, a.subcategory, a.merchant, a.tags, a.annotated_at
        FROM annotations a
        JOIN transactions t ON t.id = a.transaction_id
        WHERE t.counterparty_key IS NOT NULL
          AND a.source IN ({",".join("?" * len(_SOURCES))})
        """,
        _SOURCES,
    ).fetchall()
    suppressed = {
        (r[0], r[1]) for r in conn.execute("SELECT counterparty_key, direction FROM learned_rule_suppressions")
    }
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        group = (r["counterparty_key"], r["debit_credit"])
        if group not in suppressed:
            groups.setdefault(group, []).append(dict(r))
    out = [rule for (key, direction), g in groups.items() if (rule := _promote(key, direction, g)) is not None]
    out.sort(key=lambda r: (-r.support, r.counterparty_key, r.direction))
    return out
