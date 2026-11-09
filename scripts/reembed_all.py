"""Rebuild every stale vector (missing, from another model, or built from text
that build_embed_text no longer produces) in a DB, e.g. an eval copy.

The app does the same on startup in the background; this is for databases the
app is not serving. Incremental: rows already current are skipped.

Usage:
    DB_PATH=<db> uv run python -m scripts.reembed_all [--dry-run]
"""
from __future__ import annotations

import argparse

from finance_copilot.db.connection import get_migrated_db
from finance_copilot.pipeline.derived import reconcile_derived_data
from finance_copilot.pipeline.embed import refresh_embeddings, stale_embedding_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="show stale texts, write nothing")
    args = ap.parse_args()

    conn = get_migrated_db()
    reconcile_derived_data(conn)
    if args.dry_run:
        stale = stale_embedding_rows(conn)
        print(f"{len(stale)} stale vectors")
        for txn, text in stale[:20]:
            print(f"  {txn['txn_date']}  {text!r}")
        return
    print(refresh_embeddings(conn, progress_cb=lambda done, total: print(f"  {done}/{total}", end="\r")))


if __name__ == "__main__":
    main()
