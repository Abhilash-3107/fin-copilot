"""Compare two eval runs: metric deltas plus the per-transaction label diff.

The A/B discipline for any pipeline change: run scripts/eval.py before and
after, then diff. Exits non-zero when accuracy or auto-accept precision
regressed beyond --tolerance, so it can gate a commit.

Usage:
    uv run python -m scripts.eval_diff baseline e6_person_gate [--tolerance 0.01]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(name: str) -> tuple[dict, dict[str, dict]]:
    base = Path("eval/results")
    summary = json.loads((base / f"{name}.json").read_text())
    records = {
        r["transaction_id"]: r
        for r in map(json.loads, (base / f"{name}.records.jsonl").read_text().splitlines())
    }
    return summary, records


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--tolerance", type=float, default=0.01)
    args = p.parse_args()

    sa, ra = _load(args.a)
    sb, rb = _load(args.b)

    print(f"{'metric':24} {args.a:>14} {args.b:>14}  delta")
    for m in ("accuracy", "brier", "auto_accept_rate", "auto_accept_precision", "review_rate", "mean_latency_s"):
        va, vb = sa.get(m), sb.get(m)
        if va is None or vb is None:
            continue
        print(f"{m:24} {va:14.4f} {vb:14.4f}  {vb - va:+.4f}")

    changed = []
    for tid, a in ra.items():
        b = rb.get(tid)
        if b is None:
            continue
        if a["category"] != b["category"] or a["stage"] != b["stage"] or a["correct"] != b["correct"]:
            changed.append((tid, a, b))
    print(f"\n{len(changed)} transactions changed label/stage/outcome:")
    for _tid, a, b in changed[:30]:
        mark = "✓" if (b["correct"] and not a["correct"]) else ("✗" if (a["correct"] and not b["correct"]) else "·")
        print(
            f"  {mark} {a['raw_description'][:48]:48}  "
            f"{a['stage']}/{a['category']}({a['confidence']:.2f}) → {b['stage']}/{b['category']}({b['confidence']:.2f})"
        )

    acc_drop = sa["accuracy"] - sb["accuracy"]
    prec_a, prec_b = sa.get("auto_accept_precision") or 0, sb.get("auto_accept_precision") or 0
    if acc_drop > args.tolerance or (prec_a - prec_b) > args.tolerance:
        print(f"\nREGRESSION beyond tolerance {args.tolerance}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
