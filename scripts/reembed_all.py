"""Re-embed every annotated transaction with the current build_embed_text.

Needed after changing build_embed_text (stripping the rotating UPI reference and
the raw amount): the existing stored vectors were built with the old, noisier text
and are no longer comparable to fresh query embeddings. upsert_embedding does
INSERT OR REPLACE, so this overwrites vectors in place — no delete needed.

Read-nothing-destructive beyond overwriting vectors; annotations are untouched.

Usage:
    uv run python -m scripts.reembed_all [--dry-run]
"""
from __future__ import annotations

import argparse

import httpx

from src.config import settings
from src.db.connection import get_connection
from src.db.queries.embeddings import delete_embeddings, upsert_embedding
from src.pipeline.embed import build_embed_text, get_embeddings_batch


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="show texts, write nothing")
    ap.add_argument("--batch-size", type=int, default=32)
    args = ap.parse_args()

    conn = get_connection()
    rows = conn.execute(
        "SELECT t.* FROM transactions t "
        "JOIN annotations a ON a.transaction_id = t.id ORDER BY t.id"
    ).fetchall()
    txns = [dict(r) for r in rows]
    print(f"{len(txns)} annotated transactions to re-embed")

    if args.dry_run:
        for t in txns[:20]:
            print(f"  {t['txn_date']}  {build_embed_text(t)!r}")
        print("... (dry run, nothing written)")
        return

    model = settings.ollama_embedding_model
    done = 0
    for i in range(0, len(txns), args.batch_size):
        batch = txns[i : i + args.batch_size]
        texts = [build_embed_text(t) for t in batch]
        try:
            vectors = get_embeddings_batch(texts)
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            print(f"  batch {i}-{i+len(batch)} FAILED: {e} (re-run to fill)")
            continue
        # sqlite-vec virtual tables don't honor INSERT OR REPLACE, so clear the
        # old vector rows for this batch before re-inserting.
        delete_embeddings(conn, [t["id"] for t in batch])
        for t, vec in zip(batch, vectors):
            upsert_embedding(conn, t["id"], vec, model)
            done += 1
        conn.commit()
        print(f"  embedded {done}/{len(txns)}")

    print(f"done: {done} re-embedded")


if __name__ == "__main__":
    main()
