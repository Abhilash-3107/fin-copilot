"""Time-split evaluation harness for the auto-annotation pipeline.

Replays the full cascade (rules → rag_direct → rag_prompted → llm)
over the golden set (human-verified labels, scripts/build_golden.py), with all
retrieval and priors restricted to history strictly before each transaction's
date — the same causality rule as scripts/backtest_counterparty_prior.py, so a
replayed transaction can never see donors that didn't exist yet.

Nothing is written to the database. The pipeline's stage functions are pure
(they return AnnotationCreate without persisting), so production code paths are
exercised directly rather than re-implemented.

Reported per run and per stage:
  - accuracy (top-level category vs the human label)
  - coverage (share of golden txns the stage decided)
  - auto-accept precision/rate at the confidence threshold
  - Brier score (confidence calibration)
  - mean latency
  - the full failure list (eval/results/<name>.failures.jsonl)

Known limitation: Beta calibration reads feedback_stats as of *now* (it is not
time-split); its influence is a per-category scalar and is identical across all
experiment arms, so A/B deltas are unaffected.

Usage:
    uv run python -m scripts.eval --name baseline [--golden eval/golden.jsonl]
        [--db data/finance.db] [--limit N]
Experiment arms are configured via env vars (pydantic settings), e.g.:
    OLLAMA_NUM_CTX=4096 uv run python -m scripts.eval --name e1_numctx
    RAG_KNN_ENABLED=true uv run python -m scripts.eval --name e2_knn
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from src.config import settings
from src.db.connection import get_connection
from src.db.queries.categories import get_category_names_flat
from src.db.queries.people import list_people
from src.pipeline.annotate import (
    _normalize_category,
    _try_rag_annotation,
    rule_annotation,
)
from src.pipeline.calibration import get_calibrated_dampening
from src.pipeline.llm import annotate_transaction_llm


def _predict(conn, txn: dict, category_list: list[str], known_people) -> dict:
    """Run the cascade for one golden txn, time-split at its own date. Returns a record."""
    t0 = time.monotonic()

    rule_result, _rule_trace = rule_annotation(conn, txn, known_people)
    if rule_result is not None:
        return {
            "stage": rule_result.source,  # "rule" or "learned_rule"
            "category": rule_result.category,
            "subcategory": rule_result.subcategory,
            "confidence": rule_result.confidence,
            "latency_s": time.monotonic() - t0,
        }

    rag_result, _trace = _try_rag_annotation(
        conn, txn, category_list, before_txn_date=txn["txn_date"]
    )
    if rag_result is not None:
        return {
            "stage": rag_result.source,
            "category": rag_result.category,
            "subcategory": rag_result.subcategory,
            "confidence": rag_result.confidence,
            "latency_s": time.monotonic() - t0,
        }

    llm_result = annotate_transaction_llm(txn, category_list)
    if llm_result is not None:
        category = _normalize_category(llm_result.category, category_list)
        dampening = get_calibrated_dampening(conn, "llm", category)
        return {
            "stage": "llm",
            "category": category,
            "subcategory": llm_result.subcategory,
            "confidence": round(llm_result.confidence * dampening, 4),
            "latency_s": time.monotonic() - t0,
        }

    return {
        "stage": "failed",
        "category": None,
        "subcategory": None,
        "confidence": 0.0,
        "latency_s": time.monotonic() - t0,
    }


def run_eval(name: str, golden_path: str, db_path: str | None, limit: int | None) -> dict:
    conn = get_connection(db_path)
    category_list = get_category_names_flat(conn)
    known_people = [(p["name"], p["upi"].lower()) for p in list_people(conn) if p.get("upi")]

    golden = [json.loads(line) for line in Path(golden_path).read_text().splitlines() if line.strip()]
    if limit:
        golden = golden[:limit]

    records = []
    for i, g in enumerate(golden, 1):
        pred = _predict(conn, g, category_list, known_people)
        pred["transaction_id"] = g["id"]
        pred["txn_date"] = g["txn_date"]
        pred["raw_description"] = g["raw_description"]
        pred["gold_category"] = g["category"]
        pred["gold_subcategory"] = g.get("subcategory")
        pred["correct"] = pred["category"] == g["category"]
        records.append(pred)
        if i % 25 == 0 or i == len(golden):
            print(f"  [{name}] {i}/{len(golden)}", flush=True)

    # --- metrics ---
    thr = settings.confidence_threshold
    per_stage: dict[str, dict] = defaultdict(lambda: {"n": 0, "correct": 0, "latency": 0.0})
    brier_sum = 0.0
    auto_n = auto_correct = 0
    for r in records:
        s = per_stage[r["stage"]]
        s["n"] += 1
        s["correct"] += int(r["correct"])
        s["latency"] += r["latency_s"]
        brier_sum += (r["confidence"] - (1.0 if r["correct"] else 0.0)) ** 2
        if r["confidence"] >= thr:
            auto_n += 1
            auto_correct += int(r["correct"])

    n = len(records)
    summary = {
        "name": name,
        "n": n,
        "settings": {
            "ollama_model": settings.ollama_model,
            "ollama_num_ctx": settings.ollama_num_ctx,
            "llm_logprob_confidence": settings.llm_logprob_confidence,
        },
        "accuracy": round(sum(r["correct"] for r in records) / n, 4) if n else 0.0,
        "brier": round(brier_sum / n, 4) if n else 0.0,
        "auto_accept_rate": round(auto_n / n, 4) if n else 0.0,
        "auto_accept_precision": round(auto_correct / auto_n, 4) if auto_n else None,
        "review_rate": round((n - auto_n) / n, 4) if n else 0.0,
        "mean_latency_s": round(sum(r["latency_s"] for r in records) / n, 3) if n else 0.0,
        "per_stage": {
            k: {
                "n": v["n"],
                "coverage": round(v["n"] / n, 4),
                "accuracy": round(v["correct"] / v["n"], 4),
                "mean_latency_s": round(v["latency"] / v["n"], 3),
            }
            for k, v in sorted(per_stage.items())
        },
    }

    out_dir = Path("eval/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(summary, indent=2))
    with (out_dir / f"{name}.records.jsonl").open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    with (out_dir / f"{name}.failures.jsonl").open("w") as f:
        for r in records:
            if not r["correct"]:
                f.write(json.dumps(r) + "\n")

    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--golden", default="eval/golden.jsonl")
    p.add_argument("--db", default=None)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    run_eval(args.name, args.golden, args.db, args.limit)
