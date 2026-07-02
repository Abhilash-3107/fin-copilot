"""Re-annotate the April rag_prompted transactions N times and report variance.

Diagnostic only: separates LLM non-determinism (temperature) from a real
regression. For each run it clears + re-annotates the target transactions and
records (category, subcategory, confidence). Prints, per transaction, the set of
distinct labels seen across runs — stable labels = deterministic, varying labels =
temperature noise.

Usage: uv run python -m scripts.diff_reannotate_april [n_runs]
"""
from __future__ import annotations

import sys
from collections import defaultdict

from src.db.connection import get_connection
from src.pipeline.annotate import auto_annotate


def _april_rag_targets(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT t.id FROM annotations a JOIN transactions t ON t.id = a.transaction_id
        WHERE t.txn_date >= '2026-04-01' AND t.txn_date < '2026-05-01'
          AND a.source = 'rag_prompted'
        """
    ).fetchall()
    return [r["id"] for r in rows]


def main() -> int:
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    conn = get_connection()
    targets = _april_rag_targets(conn)
    descs = {r["id"]: r["raw_description"] for r in conn.execute(
        f"SELECT id, raw_description FROM transactions WHERE id IN ({','.join('?'*len(targets))})",
        targets,
    ).fetchall()}

    seen: dict[str, list[str]] = defaultdict(list)
    for run in range(n_runs):
        # Clear just these annotations so the pipeline re-derives them.
        conn.execute(
            f"DELETE FROM annotations WHERE transaction_id IN ({','.join('?'*len(targets))})",
            targets,
        )
        conn.commit()
        auto_annotate(conn, transaction_ids=targets)
        for r in conn.execute(
            f"""SELECT a.transaction_id, a.category, a.subcategory, a.source, round(a.confidence,2) c
                FROM annotations a WHERE a.transaction_id IN ({','.join('?'*len(targets))})""",
            targets,
        ).fetchall():
            label = f"{r['source']}:{r['category']}/{r['subcategory'] or ''}@{r['c']}"
            seen[r["transaction_id"]].append(label)
        print(f"run {run+1}/{n_runs} done", file=sys.stderr)

    conn.close()

    print(f"\n=== Label variance across {n_runs} runs ===")
    stable = varying = 0
    for tid in targets:
        labels = seen[tid]
        distinct = sorted(set(labels))
        desc = (descs.get(tid) or "")[:32]
        if len(distinct) == 1:
            stable += 1
            print(f"  STABLE  {desc:<34} {distinct[0]}")
        else:
            varying += 1
            print(f"  VARYING {desc:<34}")
            for d in distinct:
                print(f"          {labels.count(d)}x {d}")
    print(f"\nstable={stable}  varying={varying}  of {len(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
