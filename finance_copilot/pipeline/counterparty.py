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

import bisect
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
    mix refunds into that payee's learned rule and prior). Use
    counterparty_identity() when a stored, resolved key may be available.
    """
    return identity_from_parsed(parse_description(raw_description))


def identity_from_parsed(parsed: ParsedDescription) -> str | None:
    """normalize_identity for an already-parsed description."""
    if parsed.rail != "upi" or not parsed.counterparty:
        return None
    return parsed.counterparty.upper()


def _prefix_compatible(a: str, b: str) -> bool:
    """Whether a and b can be the same payee printed at two truncation widths."""
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return (
        len(short) >= settings.counterparty_prefix_min_len
        and len(long_) - len(short) <= settings.counterparty_prefix_max_delta
        and long_.startswith(short)
    )


def pick_canonical_key(raw_key: str, candidates: Iterable[str]) -> str:
    """Resolve raw_key against existing keys: itself if already known, else the
    single prefix-compatible existing key, else itself (no match or ambiguous).

    Short names (below counterparty_prefix_min_len) are never truncated by the
    bank, so they only ever match exactly.
    """
    compatible: set[str] = set()
    for key in candidates:
        if key == raw_key:
            return raw_key
        if _prefix_compatible(raw_key, key):
            compatible.add(key)
    if len(compatible) == 1:
        return compatible.pop()
    return raw_key


def resolve_counterparty_key(conn: sqlite3.Connection, raw_key: str | None) -> str | None:
    """Fold a truncation variant onto the counterparty key already stored for it.

    Candidates share the first counterparty_prefix_min_len characters, found by
    an index range scan on transactions.counterparty_key.
    """
    if not raw_key:
        return None
    if len(raw_key) < settings.counterparty_prefix_min_len:
        return raw_key
    stem = raw_key[: settings.counterparty_prefix_min_len]
    rows = conn.execute(
        "SELECT DISTINCT counterparty_key FROM transactions "
        "WHERE counterparty_key >= ? AND counterparty_key < ?",
        (stem, stem + "\U0010ffff"),
    ).fetchall()
    return pick_canonical_key(raw_key, (r[0] for r in rows))


class KeyResolver:
    """In-memory resolve_counterparty_key for bulk re-derivation, where the
    stored keys are the stale ones being replaced. Feed rows in date order."""

    def __init__(self) -> None:
        self._sorted: list[str] = []

    def resolve(self, raw_key: str | None) -> str | None:
        if not raw_key:
            return None
        if len(raw_key) >= settings.counterparty_prefix_min_len:
            stem = raw_key[: settings.counterparty_prefix_min_len]
            lo = bisect.bisect_left(self._sorted, stem)
            hi = bisect.bisect_left(self._sorted, stem + "\U0010ffff")
            key = pick_canonical_key(raw_key, self._sorted[lo:hi])
        else:
            key = raw_key
        i = bisect.bisect_left(self._sorted, key)
        if i == len(self._sorted) or self._sorted[i] != key:
            self._sorted.insert(i, key)
        return key


def counterparty_identity(conn: sqlite3.Connection, txn: dict) -> str | None:
    """The resolved counterparty key for a transaction: the stored column when the
    row has one, else parsed from the description and resolved against the DB
    (eval rows and not-yet-stored transactions)."""
    stored = txn.get("counterparty_key")
    if stored:
        return stored
    return resolve_counterparty_key(conn, normalize_identity(txn.get("raw_description")))


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
    *,
    before_txn_date: str | None = None,
    exclude_transaction_id: str | None = None,
) -> list[dict]:
    """Fetch prior (category, source) labels for a counterparty identity.

    Only annotated transactions are returned. `before_txn_date` and
    `exclude_transaction_id` enforce causality: a backtest replaying history in time
    order must only see labels that existed *before* the transaction being scored,
    otherwise the prior leaks its own answer.

    Identity matching uses the indexed transactions.counterparty_key column,
    which holds the resolved key (truncation variants folded together) computed
    at ingest and re-derived on parser upgrades (pipeline/derived.py).
    """
    rows = conn.execute(
        """
        SELECT t.id AS transaction_id, t.txn_date, t.raw_description,
               a.category, a.source
        FROM annotations a
        JOIN transactions t ON t.id = a.transaction_id
        WHERE t.counterparty_key = ?
          AND a.category IS NOT NULL
          AND (? IS NULL OR t.txn_date < ?)
          AND (? IS NULL OR t.id != ?)
        """,
        (identity, before_txn_date, before_txn_date, exclude_transaction_id, exclude_transaction_id),
    ).fetchall()

    return [
        {
            "transaction_id": row["transaction_id"],
            "txn_date": row["txn_date"],
            "category": row["category"],
            "source": row["source"],
        }
        for row in rows
    ]


def counterparty_prior(
    conn: sqlite3.Connection,
    txn: dict,
    *,
    before_txn_date: str | None = None,
) -> CounterpartyPrior:
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
    identity = counterparty_identity(conn, txn)
    if identity is None:
        return CounterpartyPrior()

    history = counterparty_history(
        conn,
        identity,
        before_txn_date=before_txn_date or txn.get("txn_date"),
        exclude_transaction_id=txn.get("id"),
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
