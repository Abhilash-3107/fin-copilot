"""Export the golden evaluation set: every human-verified (manual) annotation.

Each line of eval/golden.jsonl is one transaction with its human-final label.
`original_source` (when present) records which machine stage the human corrected,
so the golden set doubles as a confirmed-machine-error corpus.

Usage:
    uv run python -m scripts.build_golden [--db data/finance.db] [--out eval/golden.jsonl]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.db.connection import get_connection


def build_golden(db_path: str | None, out_path: str) -> int:
    conn = get_connection(db_path)
    rows = conn.execute(
        """
        SELECT t.id, t.txn_date, t.raw_description, t.amount, t.debit_credit,
               t.upi_meta, t.statement_id,
               a.category, a.subcategory, a.merchant, a.source, a.original_source
        FROM annotations a
        JOIN transactions t ON t.id = a.transaction_id
        WHERE a.source = 'manual'
        ORDER BY t.txn_date, t.id
        """
    ).fetchall()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for r in rows:
            f.write(json.dumps(dict(r)) + "\n")
    return len(rows)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=None)
    p.add_argument("--out", default="eval/golden.jsonl")
    args = p.parse_args()
    n = build_golden(args.db, args.out)
    print(f"wrote {n} golden rows to {args.out}")
