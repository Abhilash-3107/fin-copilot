"""Run-level summary over stored reasoning traces.

Aggregates annotations.reasoning across the annotated corpus (or one statement)
into the dev-mode insight surface: a stage funnel, the best-similarity and
final-confidence distributions drawn against the thresholds each is gated by, and
near-miss lists — transactions sitting just below a gate, i.e. the ones a small
threshold change would flip. Individual traces explain one decision; these show
where the mass sits relative to a knob, which is what informs setting it.

Reads only what auto_annotate already persisted (traces are captured
unconditionally now), so no re-running the pipeline: the summary reflects the
last state of the corpus and updates as annotations are added or corrected.
"""
from __future__ import annotations

import json
import sqlite3

from src.config import settings

# Ordered so the funnel reads cheapest/most-certain → most-expensive, matching the
# pipeline's own stage order (rule → learned_rule → rag_* → llm).
_STAGE_ORDER = ["rule", "learned_rule", "rag_direct", "rag_knn", "rag_prompted", "llm"]

# Histogram resolution over the [0, 1] score range. 20 bins = 0.05 wide, fine
# enough to see a threshold's neighbourhood without looking noisy on a few-hundred
# transaction corpus.
_BINS = 20

# How close to a gate counts as a "near miss". 0.05 = one histogram bin; a value
# within this of a threshold would flip if the knob moved by that much.
_NEAR_MISS_BAND = 0.05


def _histogram(values: list[float]) -> list[int]:
    """Bin values in [0, 1] into _BINS equal buckets. Out-of-range is clamped."""
    counts = [0] * _BINS
    for v in values:
        idx = min(_BINS - 1, max(0, int(v * _BINS)))
        counts[idx] += 1
    return counts


def _load_traces(conn: sqlite3.Connection, statement_id: str | None) -> list[dict]:
    """Fetch (annotation, transaction, parsed-trace) rows for annotated txns.

    Rows with no reasoning (annotated before trace capture existed) are kept but
    carry trace=None, so the funnel still counts them under their source.
    """
    sql = """
        SELECT a.transaction_id, a.source, a.confidence, a.category, a.merchant,
               a.reasoning, t.raw_description, t.amount, t.txn_date
        FROM annotations a
        JOIN transactions t ON t.id = a.transaction_id
    """
    params: tuple = ()
    if statement_id:
        sql += " WHERE t.statement_id = ?"
        params = (statement_id,)
    rows = []
    for row in conn.execute(sql, params).fetchall():
        d = dict(row)
        raw = d.pop("reasoning", None)
        try:
            d["trace"] = json.loads(raw) if raw else None
        except (TypeError, json.JSONDecodeError):
            d["trace"] = None
        rows.append(d)
    return rows


def run_summary(conn: sqlite3.Connection, statement_id: str | None = None) -> dict:
    """Aggregate stored traces into the run-level insight payload.

    Only model-sourced annotations count toward the pipeline funnel/distributions;
    manual and imported labels are excluded (they weren't decided by the pipeline).
    """
    rows = [r for r in _load_traces(conn, statement_id) if r["source"] in _STAGE_ORDER]
    total = len(rows)

    threshold = settings.confidence_threshold

    # --- Stage funnel: count per stage, plus how many landed in review (below the
    #     confidence threshold) vs auto-accepted. review_rate is the headline the
    #     stage mix explains.
    stages: dict[str, dict] = {
        s: {"stage": s, "count": 0, "auto_accepted": 0, "review": 0} for s in _STAGE_ORDER
    }
    best_similarities: list[float] = []
    final_confidences: list[float] = []
    near_miss_similarity: list[dict] = []
    near_miss_confidence: list[dict] = []

    for r in rows:
        st = stages[r["source"]]
        st["count"] += 1
        conf = r["confidence"] if r["confidence"] is not None else 0.0
        final_confidences.append(conf)
        if conf < threshold:
            st["review"] += 1
        else:
            st["auto_accepted"] += 1

        # Confidence near-miss: just below the review threshold → a small lift
        # would auto-accept it. Surfaced so the user can see what raising trust costs.
        if threshold - _NEAR_MISS_BAND <= conf < threshold:
            near_miss_confidence.append({
                "transaction_id": r["transaction_id"],
                "raw_description": r["raw_description"],
                "category": r["category"],
                "source": r["source"],
                "confidence": round(conf, 4),
            })

        trace = r["trace"] or {}
        best_sim = trace.get("best_similarity")
        if best_sim is not None:
            best_similarities.append(best_sim)
            # Similarity near-miss: below rag_direct_threshold but within a bin of
            # it → would have skipped the LLM and copied a neighbour if the knob dropped.
            direct = trace.get("thresholds", {}).get("rag_direct_threshold", settings.rag_direct_threshold)
            if direct - _NEAR_MISS_BAND <= best_sim < direct:
                near_miss_similarity.append({
                    "transaction_id": r["transaction_id"],
                    "raw_description": r["raw_description"],
                    "category": r["category"],
                    "source": r["source"],
                    "best_similarity": round(best_sim, 4),
                })

    stage_funnel = [stages[s] for s in _STAGE_ORDER if stages[s]["count"] > 0]

    review_count = sum(s["review"] for s in stage_funnel)
    auto_count = sum(s["auto_accepted"] for s in stage_funnel)

    # Sort near-miss lists by proximity to the gate (closest first) and cap the
    # payload — this is a "here are the tuning candidates" list, not a full dump.
    near_miss_similarity.sort(key=lambda m: -m["best_similarity"])
    near_miss_confidence.sort(key=lambda m: -m["confidence"])

    return {
        "total": total,
        "review_count": review_count,
        "auto_accepted_count": auto_count,
        "review_rate": round(review_count / total, 4) if total else 0.0,
        "confidence_threshold": threshold,
        "stage_funnel": stage_funnel,
        "similarity": {
            "bins": _BINS,
            "counts": _histogram(best_similarities),
            "n": len(best_similarities),
            "thresholds": {
                "rag_similarity_floor": settings.rag_similarity_floor,
                "rag_direct_threshold": settings.rag_direct_threshold,
            },
        },
        "confidence": {
            "bins": _BINS,
            "counts": _histogram(final_confidences),
            "n": len(final_confidences),
            "thresholds": {"confidence_threshold": threshold},
        },
        "near_miss_similarity": near_miss_similarity[:15],
        "near_miss_confidence": near_miss_confidence[:15],
        "near_miss_band": _NEAR_MISS_BAND,
    }
