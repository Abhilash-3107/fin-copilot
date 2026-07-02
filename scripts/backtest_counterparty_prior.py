"""Causal backtest for the counterparty recurrence prior — the decision gate.

Replays annotated UPI transactions in chronological order. For each one, it
computes the counterparty prior using ONLY labels dated strictly before it
(right-censored, no leakage), then asks: when the prior is *established*, does its
category agree with the ground-truth label?

Ground truth = the annotation currently stored (human-corrected where it mattered).
We only score against trusted labels (manual/rule/imported); machine-only labels
are excluded from the truth set so we don't grade the prior against an unverified
guess.

The point is to find out — before touching the pipeline — whether the mature-history
recurrence signal (4.2x vs 1.06x) survives causal evaluation, or whether it was
survivorship bias. If the established prior is not both high-precision and
meaningfully-covering on the ambiguous personal-name cohort, we STOP.

Read-only. Writes nothing. Usage:
    uv run python -m scripts.backtest_counterparty_prior [--month 2026-04]
"""
from __future__ import annotations

import argparse
from collections import Counter

from src.config import settings
from src.db.connection import get_connection
from src.pipeline.counterparty import counterparty_prior, normalize_identity

# Trusted ground-truth sources. We grade the prior only against these.
_TRUSTED = {"manual", "rule", "imported"}
# The cohort the prior is meant to help: bare personal-name UPIs that the
# embedding can't separate. We approximate "personal name" as a UPI whose name
# segment is not a known merchant token. Kept deliberately simple — the headline
# metric is overall, this cohort is the stress test.
_MERCHANT_HINTS = (
    "ZOMATO", "SWIGGY", "ZEPTO", "BLINKIT", "DISTRICT DINING", "RAPIDO", "UBER",
    "OLA", "URBAN", "LIMITED", "LTD", "ENTERP", "OBEROI", "PVR", "RAILWAY",
    "SPOTIFY", "LICIOUS", "MARKETPLA", "BHARATPE", "INDMONEY", "PAYTM", "APPLE",
    "LINKEDIN", "PRACTO", "NETFLIX", "AMAZON", "INOX",
)


def _looks_personal(identity: str) -> bool:
    return not any(h in identity for h in _MERCHANT_HINTS)


def _score(conn, rows, month: str | None) -> dict:
    """Score the prior over the timeline with the current settings. Returns counters."""
    stats = {"overall": Counter(), "personal": Counter()}
    mistakes: list[tuple] = []
    for r in rows:
        if r["source"] not in _TRUSTED:
            continue
        if month and not r["txn_date"].startswith(month):
            continue
        identity = normalize_identity(r["raw_description"])
        if identity is None:
            continue
        txn = {"id": r["id"], "raw_description": r["raw_description"], "txn_date": r["txn_date"]}
        prior = counterparty_prior(conn, txn)
        keys = ["overall"] + (["personal"] if _looks_personal(identity) else [])
        for bk in keys:
            stats[bk]["scored"] += 1
            if prior.established:
                stats[bk]["established"] += 1
                if prior.category == r["category"]:
                    stats[bk]["correct"] += 1
                else:
                    stats[bk]["wrong"] += 1
                    if bk == "personal":
                        mistakes.append((r["txn_date"], identity, prior.category,
                                         r["category"], prior.n_prior, prior.probability))
    return {"stats": stats, "mistakes": mistakes}


def _sweep(conn, rows, month: str | None) -> None:
    """Grid-sweep min_observations x dominance_floor; print precision/coverage."""
    print("\n=== THRESHOLD SWEEP (personal-name cohort) ===")
    print(f"{'min_obs':>7} {'floor':>6} {'cov':>7} {'prec':>7} {'est':>5} {'wrong':>6}")
    for min_obs in (2, 3, 4, 5):
        for floor in (0.5, 0.6, 0.65, 0.7):
            settings.counterparty_min_observations = min_obs
            settings.counterparty_dominance_floor = floor
            res = _score(conn, rows, month)
            p = res["stats"]["personal"]
            cov = p["established"] / p["scored"] if p["scored"] else 0.0
            prec = p["correct"] / p["established"] if p["established"] else 0.0
            print(f"{min_obs:>7} {floor:>6.2f} {cov:>6.1%} {prec:>6.1%} "
                  f"{p['established']:>5} {p['wrong']:>6}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="Restrict scoring to txns in this month, e.g. 2026-04")
    ap.add_argument("--sweep", action="store_true", help="Grid-sweep thresholds and exit")
    args = ap.parse_args()

    conn = get_connection()

    # All annotated UPI txns in chronological order — the replay timeline.
    rows = conn.execute(
        """
        SELECT t.id, t.txn_date, t.raw_description, a.category, a.source
        FROM annotations a JOIN transactions t ON t.id = a.transaction_id
        WHERE t.raw_description LIKE 'UPI/%' AND a.category IS NOT NULL
        ORDER BY t.txn_date ASC, t.id ASC
        """
    ).fetchall()

    if args.sweep:
        _sweep(conn, rows, args.month)
        conn.close()
        return 0

    res = _score(conn, rows, args.month)
    stats, mistakes = res["stats"], res["mistakes"]
    conn.close()

    def _report(name: str, c: Counter) -> None:
        scored = c["scored"]
        est = c["established"]
        correct = c["correct"]
        coverage = est / scored if scored else 0.0
        precision = correct / est if est else 0.0
        print(f"\n=== {name} cohort ===")
        print(f"  scored (trusted-truth txns):     {scored}")
        print(f"  prior established:                {est}  (coverage {coverage:.1%})")
        print(f"  established & correct:            {correct}")
        print(f"  established & wrong:              {c['wrong']}")
        print(f"  PRECISION when established:       {precision:.1%}")

    print("Causal backtest — counterparty recurrence prior")
    print(f"settings: prior_weight={settings.counterparty_prior_weight} "
          f"min_obs={settings.counterparty_min_observations} "
          f"dominance_floor={settings.counterparty_dominance_floor}")
    _report("OVERALL", stats["overall"])
    _report("PERSONAL-NAME", stats["personal"])

    if mistakes:
        print("\n--- personal-cohort mistakes (prior_cat != truth) ---")
        for d, ident, pcat, tcat, n, p in mistakes:
            print(f"  {d}  {ident:<18}  prior={pcat:<12} truth={tcat:<12} n={n} p={p}")

    # Decision-gate verdict.
    p = stats["personal"]
    prec = p["correct"] / p["established"] if p["established"] else 0.0
    cov = p["established"] / p["scored"] if p["scored"] else 0.0
    print("\n=== DECISION GATE ===")
    if p["established"] >= 5 and prec >= 0.85:
        print(f"  PASS — personal-cohort precision {prec:.1%} at {cov:.1%} coverage "
              f"({p['established']} established cases). Signal survives causal eval.")
    else:
        print(f"  STOP — insufficient evidence: precision {prec:.1%}, coverage {cov:.1%}, "
              f"{p['established']} established cases. Do not wire into pipeline yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
