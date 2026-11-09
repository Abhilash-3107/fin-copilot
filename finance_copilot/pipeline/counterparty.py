"""Counterparty recurrence prior — a late-fused, out-of-band signal for the RAG stage.

The embedding/KNN retriever is blind to *recurrence*: two payments to the same
person and one to a random cab driver are all "a UPI to a personal name", so the
neighbour vote is dominated by base rates (cab drivers vastly outnumber recurring
contacts in the donor pool). But the user's own history separates them cleanly —
cab counterparties are one-and-done (~1.06 txns each) while family/friends recur
(~4.2 txns each).

This module turns that into a per-counterparty category prior, computed from the
user's *prior* annotations only (no leakage), via a Beta–Binomial / empirical-Bayes
shrinkage so it degrades gracefully:

  - n_prior == 0 (new user, never-seen counterparty) → estimate == the population
    base rate → contributes nothing → cold-start behaviour is identical to today.
  - n_prior grows → the counterparty's own observed labels take over and the prior
    fades. (Apple BayesCNS / Amazon empirical-Bayes cold-start pattern.)

The prior is *fused at the decision layer* (confidence/routing in rag_prompted),
never baked into the embedding and never used to override the RAG label.

Identity key: the payee name parsed out of the UPI description
(parsers/description.py), upper-cased and whitespace-collapsed. The bank format
carries no VPA (name@bank appears in 1/568 rows), so the printed name is the only
stable counterparty handle. Banks print it truncated to a fixed width, and the
width can change with the layout (Kotak: 15 chars, 14 once an IFSC bank prefix
was added in June 2026), so one payee can appear as 'OBEROIFC TUCKSH' and
'OBEROIFC TUCKS'. resolve_counterparty_key folds such truncation variants onto
the first-seen key at ingest; it only merges when exactly one existing key is
prefix-compatible, so it can under-merge (weaker prior, the safe failure mode)
but never fuses two different people.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field

from finance_copilot.config import settings
from finance_copilot.parsers.description import ParsedDescription, parse_description

# Sources whose labels are trustworthy enough to seed a counterparty prior at full
# weight; machine guesses are downweighted so a recurring RAG-mislabeled
# counterparty can't bootstrap its own bad prior.
from finance_copilot.pipeline.sources import TRUSTED_SOURCES as _TRUSTED_SOURCES


def normalize_identity(raw_description: str | None) -> str | None:
    """Return the raw (unresolved) counterparty key from a UPI description, or None.

    The parsed payee name, upper-cased and whitespace-collapsed. None for non-UPI
    rails and UPI reversals (a reversal is a credit; sharing the payee's key would
    mix refunds into that payee's learned rule and prior). Stored rows carry
    the resolved key in transactions.counterparty_key; read that instead.
    """
    return identity_from_parsed(parse_description(raw_description))


def identity_from_parsed(parsed: ParsedDescription) -> str | None:
    """normalize_identity for an already-parsed description."""
    if parsed.rail != "upi" or not parsed.counterparty:
        return None
    return parsed.counterparty.upper()


def truncation_variants(a: str, b: str) -> bool:
    """Whether a and b can be one payee printed at two truncation widths.

    Banks cut the payee field at a fixed width (Kotak: 15, or 14 when an IFSC
    prefix is present), so the shorter key must itself be a cut: at least
    counterparty_truncated_min_len chars, and the longer adds one character, or
    two when the cut landed on a space that whitespace-collapse then dropped
    ('AGODA COMPANY' vs 'AGODA COMPANY P'). 'MOHAMMED ALI' (a full 12-char name)
    is never a cut of 'MOHAMMED ALI KH'.
    """
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < settings.counterparty_truncated_min_len or not long_.startswith(short):
        return False
    gap = len(long_) - len(short)
    return gap == 1 or (gap == 2 and long_[len(short)] == " ")


def _stem(key: str) -> str:
    return key[: settings.counterparty_truncated_min_len]


def _unique_partner(key: str, candidates: Iterable[str]) -> str | None:
    """The one other key that key is a truncation variant of, or None when it
    has none or several (ambiguous: never guess between people)."""
    partners = {c for c in candidates if c != key and truncation_variants(key, c)}
    return partners.pop() if len(partners) == 1 else None


def canonical_keys(raw_keys: Iterable[str]) -> dict[str, str]:
    """Map every raw key to its canonical key: the longest spelling of a payee.

    Two keys merge only when each is the other's unique truncation variant, so
    the result does not depend on the order statements were uploaded in, and
    ingest (resolve_counterparty_key) reaches the same answer incrementally.
    """
    by_stem: dict[str, set[str]] = {}
    for key in raw_keys:
        by_stem.setdefault(_stem(key), set()).add(key)
    mapping: dict[str, str] = {}
    for keys in by_stem.values():
        for key in keys:
            partner = _unique_partner(key, keys)
            if partner is not None and _unique_partner(partner, keys) == key:
                mapping[key] = max(key, partner, key=len)
            else:
                mapping[key] = key
    return mapping


def resolve_counterparty_key(conn: sqlite3.Connection, raw_key: str | None) -> str | None:
    """The canonical key for a payee seen at ingest. Caller commits.

    When the new spelling is the longer one, the stored rows move to it (with
    any learned-rule suppression), keeping "longest spelling wins" true no
    matter which statement arrived first.
    """
    if raw_key is None:
        return None
    if len(raw_key) < settings.counterparty_truncated_min_len:
        return raw_key
    stem = _stem(raw_key)
    stored = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT counterparty_key FROM transactions "
            "WHERE counterparty_key >= ? AND counterparty_key < ?",
            (stem, stem + "\U0010ffff"),
        )
    ]
    if raw_key in stored:
        return raw_key
    partner = _unique_partner(raw_key, stored)
    if partner is None or _unique_partner(partner, [*stored, raw_key]) != raw_key:
        return raw_key
    if len(partner) >= len(raw_key):
        return partner
    rekey_counterparty(conn, partner, raw_key)
    return raw_key


def rekey_counterparty(conn: sqlite3.Connection, old_key: str, new_key: str) -> None:
    """Move every row (and a learned-rule dismissal) from one key to another. Caller commits."""
    conn.execute("UPDATE transactions SET counterparty_key = ? WHERE counterparty_key = ?", (new_key, old_key))
    conn.execute(
        "UPDATE OR IGNORE learned_rule_suppressions SET counterparty_key = ? WHERE counterparty_key = ?",
        (new_key, old_key),
    )


def _label_weight(source: str | None) -> float:
    """Weight a prior label by its source: trusted = 1.0, machine guess downweighted."""
    return 1.0 if source in _TRUSTED_SOURCES else settings.rag_machine_donor_weight


@dataclass
class CounterpartyPrior:
    """The recurrence-based category prior for one counterparty.

    n_prior         — count of prior annotated txns to this counterparty (any source)
    trusted_weight  — summed source-weight of those labels (the evidence mass)
    category        — the dominant prior category, or None when there is no evidence
    probability     — shrunk P(category | counterparty) in [0, 1]
    histogram       — weighted category → weight map (for tracing / debugging)

    `established` gates whether the prior is strong enough to influence routing: it
    requires both a minimum number of observations and a dominant category. Below
    that bar the prior is inert (cold-start safety).
    """
    n_prior: int = 0
    trusted_weight: float = 0.0
    category: str | None = None
    probability: float = 0.0
    histogram: dict[str, float] = field(default_factory=dict)

    @property
    def established(self) -> bool:
        return (
            self.category is not None
            and self.n_prior >= settings.counterparty_min_observations
            and self.probability >= settings.counterparty_dominance_floor
        )


def counterparty_history(
    conn: sqlite3.Connection,
    identity: str,
    direction: str,
    *,
    before_txn_date: str,
    exclude_transaction_id: str | None = None,
) -> list[dict]:
    """Prior (category, source) labels for one payee in one direction.

    Direction is part of identity for history: a refund from a merchant says
    nothing about what the user spends there. Causal: only labels dated before
    before_txn_date, so a replay in the eval sees what production saw.
    """
    rows = conn.execute(
        """
        SELECT t.id AS transaction_id, t.txn_date, t.debit_credit,
               a.category, a.source
        FROM annotations a
        JOIN transactions t ON t.id = a.transaction_id
        WHERE t.counterparty_key = ? AND t.debit_credit = ? AND t.txn_date < ?
          AND t.id IS NOT ?
        """,
        (identity, direction, before_txn_date, exclude_transaction_id),
    ).fetchall()

    return [
        {
            "transaction_id": row["transaction_id"],
            "txn_date": row["txn_date"],
            "debit_credit": row["debit_credit"],
            "category": row["category"],
            "source": row["source"],
        }
        for row in rows
    ]


def counterparty_prior(conn: sqlite3.Connection, txn: dict) -> CounterpartyPrior:
    """Compute the empirical-Bayes recurrence prior for a transaction's counterparty.

    Returns an inert CounterpartyPrior (category=None, probability=0) when the
    counterparty is unknown or has no prior labels — so a new user, or any first-time
    counterparty, gets no nudge.

    The shrinkage estimate for the dominant category c is:

        P(c | counterparty) = (m * base + w_c) / (m + W)

    where W is the total trusted-weighted label mass, w_c the winner's mass, base the
    uninformed population base rate (uniform over the observed categories → at W=0
    this is undefined and we short-circuit to inert), and m the prior pseudo-count
    (settings.counterparty_prior_weight). With m>0 a single observation cannot drive
    the probability to 1.0; it takes several consistent labels to clear the
    dominance floor — exactly the recurrence requirement.
    """
    identity = txn["counterparty_key"]
    if identity is None:
        return CounterpartyPrior()

    history = counterparty_history(
        conn, identity, txn["debit_credit"],
        before_txn_date=txn["txn_date"], exclude_transaction_id=txn["id"],
    )
    if not history:
        return CounterpartyPrior()

    histogram: dict[str, float] = {}
    total_weight = 0.0
    for h in history:
        cat = h["category"]
        w = _label_weight(h["source"])
        histogram[cat] = histogram.get(cat, 0.0) + w
        total_weight += w

    if total_weight <= 0.0:
        return CounterpartyPrior(n_prior=len(history))

    winner, winner_weight = max(histogram.items(), key=lambda kv: kv[1])

    # Uninformed base rate: uniform over the categories actually seen for this
    # counterparty. Shrinks the winner toward "no single category dominates" when
    # evidence is thin, without importing any cross-user/global distribution.
    base = 1.0 / len(histogram)
    m = settings.counterparty_prior_weight
    probability = (m * base + winner_weight) / (m + total_weight)

    return CounterpartyPrior(
        n_prior=len(history),
        trusted_weight=round(total_weight, 4),
        category=winner,
        probability=round(probability, 4),
        histogram={k: round(v, 4) for k, v in histogram.items()},
    )
