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

Identity key: the normalized name segment of the UPI description. The bank format
here carries no VPA (name@bank appears in 1/568 rows); the 2nd "/"-segment of
`UPI/NAME/ref/note` is the only stable counterparty handle, and it is consistently
truncated so it collides with itself. Truncation can *false-split* one entity into
two (ZEPTO vs ZEPTO MARKETPLA) — that only under-counts recurrence (weakens the
prior), it never over-merges two different people, so it is the safe failure mode.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from src.config import settings

# Sources whose labels are trustworthy enough to seed a counterparty prior at full
# weight; machine guesses are downweighted so a recurring RAG-mislabeled
# counterparty can't bootstrap its own bad prior.
from src.pipeline.sources import TRUSTED_SOURCES as _TRUSTED_SOURCES


def normalize_identity(raw_description: str | None) -> str | None:
    """Return a stable counterparty key from a UPI description, or None.

    Keys on the 2nd segment of `UPI/NAME/ref/note`, upper-cased and whitespace-
    collapsed. Returns None for non-UPI or unparseable descriptions, in which case
    the caller treats the counterparty as unknown (no prior).
    """
    if not raw_description:
        return None
    parts = raw_description.split("/")
    # Expect at least UPI / NAME / ... — segment[1] is the counterparty name.
    if len(parts) < 2 or parts[0].strip().upper() != "UPI":
        return None
    name = " ".join(parts[1].split()).strip().upper()
    return name or None


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

    Identity matching uses the indexed transactions.counterparty_key column
    (computed via normalize_identity at ingest, backfilled by migration 017) —
    an indexed lookup instead of the previous full scan of all UPI annotations.
    The Python-side identity check stays as a cheap exactness guard for the few
    matched rows (the SQL backfill's whitespace handling is slightly looser).
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

    out: list[dict] = []
    for row in rows:
        if normalize_identity(row["raw_description"]) == identity:
            out.append({
                "transaction_id": row["transaction_id"],
                "txn_date": row["txn_date"],
                "category": row["category"],
                "source": row["source"],
            })
    return out


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
    identity = normalize_identity(txn.get("raw_description"))
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
