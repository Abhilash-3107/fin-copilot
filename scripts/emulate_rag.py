"""Emulate the rag_prompted path to measure misclassification before/after fixes.

For a given statement, re-runs RAG retrieval for every transaction that the live
pipeline annotated via source='rag_prompted' (or, with --all, every transaction
that would fall through to RAG), and reports:

  - the top-K retrieved neighbors with their category, source, and similarity
  - the majority category among annotated neighbors (and its count)
  - the annotation currently stored for the query transaction
  - whether a consensus shortcut (>=K trusted neighbors agreeing) would fire

This touches no production code and writes nothing — it's a read-only diagnostic
so we can diff behavior on the same statement before and after the fixes land.

Usage:
    uv run python -m scripts.emulate_rag <statement_id> [--all] [--llm]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from src.config import settings
from src.db.connection import get_connection
from src.db.queries.annotations import insert_annotation  # noqa: F401  (kept for parity)
from src.db.queries.categories import get_category_names_flat
from src.db.queries.embeddings import find_similar
from src.pipeline.annotate import (
    _annotations_by_transaction,
    _build_examples_from_similar,
    _dedup_donors,
    _donor_transactions,
    _majority_category,
    _weighted_trusted_vote,
)
from src.pipeline.embed import build_embed_text, get_embedding_single
from src.pipeline.llm import (
    annotate_transaction_llm_with_examples,
)

# Mirror the trust set used in annotate._try_rag_annotation.
_TRUSTED_SOURCES = {"manual", "rule", "imported"}


def _stored_annotation(conn, txn_id: str) -> dict | None:
    row = conn.execute(
        "SELECT category, subcategory, merchant, source, confidence "
        "FROM annotations WHERE transaction_id = ?",
        (txn_id,),
    ).fetchone()
    return dict(row) if row else None


def _neighbors(conn, txn: dict) -> list[dict]:
    """Retrieve top-K neighbors and attach each one's stored annotation."""
    vec = get_embedding_single(build_embed_text(txn))
    similar = find_similar(
        conn, vec, top_k=settings.rag_top_k, exclude_transaction_ids=[txn["id"]]
    )
    out = []
    for m in similar:
        ann = _stored_annotation(conn, m["transaction_id"]) or {}
        tx = conn.execute(
            "SELECT raw_description, amount FROM transactions WHERE id = ?",
            (m["transaction_id"],),
        ).fetchone()
        out.append(
            {
                "similarity": 1.0 - m["distance"],
                "category": ann.get("category"),
                "subcategory": ann.get("subcategory"),
                "source": ann.get("source"),
                "amount": tx["amount"] if tx else None,
                "desc": tx["raw_description"] if tx else "?",
            }
        )
    return out


def _majority(neighbors: list[dict]) -> tuple[str | None, int, int]:
    """Return (majority_category, agreeing_count, trusted_agreeing_count)."""
    cats = [n["category"] for n in neighbors if n["category"]]
    if not cats:
        return None, 0, 0
    top_cat, count = Counter(cats).most_common(1)[0]
    trusted = sum(
        1
        for n in neighbors
        if n["category"] == top_cat and n["source"] in _TRUSTED_SOURCES
    )
    return top_cat, count, trusted


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("statement_id")
    ap.add_argument(
        "--all",
        action="store_true",
        help="emulate every transaction, not just stored source='rag_prompted'",
    )
    ap.add_argument(
        "--llm",
        action="store_true",
        help="also call the LLM with both the old and new prompt and print the category each yields",
    )
    args = ap.parse_args()

    conn = get_connection()
    try:
        conn.execute("SELECT vec_version()")
    except Exception:
        print("ERROR: sqlite-vec not loaded — retrieval unavailable.", file=sys.stderr)
        return 1

    if args.all:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE statement_id = ? ORDER BY amount",
            (args.statement_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT t.* FROM transactions t JOIN annotations a ON a.transaction_id = t.id "
            "WHERE t.statement_id = ? AND a.source = 'rag_prompted' ORDER BY t.amount",
            (args.statement_id,),
        ).fetchall()

    print(f"Emulating {len(rows)} transaction(s) for statement {args.statement_id}\n")
    category_list = get_category_names_flat(conn) if args.llm else []

    for row in rows:
        txn = dict(row)
        stored = _stored_annotation(conn, txn["id"])
        neighbors = _neighbors(conn, txn)
        maj_cat, maj_count, trusted_count = _majority(neighbors)

        stored_str = (
            f"{stored['category']}/{stored.get('subcategory')} [{stored['source']}]"
            if stored
            else "—"
        )
        print("=" * 88)
        print(f"QUERY  {txn['raw_description'][:60]!r}  amt={txn['amount']} {txn['debit_credit']}")
        print(f"  stored:    {stored_str}")
        print(
            f"  majority:  {maj_cat}  (agree={maj_count}/{len(neighbors)}, trusted={trusted_count})"
        )
        for n in neighbors:
            print(
                f"    sim={n['similarity']:.4f}  {n['category']}/{n['subcategory']} "
                f"[{n['source']}]  amt={n['amount']}  {n['desc'][:45]!r}"
            )

        if args.llm:
            _diff_llm(conn, txn, category_list)
    return 0


def _diff_llm(conn, txn: dict, category_list: list[str]) -> None:
    """Run the LLM with the old prompt (no hint) vs the new prompt (majority hint),
    then apply the production post-processing (dedup vote → off-example / defer caps)
    so the printed NEW category+confidence matches what the pipeline would store."""
    vec = get_embedding_single(build_embed_text(txn))
    similar = find_similar(
        conn, vec, top_k=settings.rag_top_k, exclude_transaction_ids=[txn["id"]]
    )
    ann_by = _annotations_by_transaction(conn, [m["transaction_id"] for m in similar])
    examples = _build_examples_from_similar(conn, similar, ann_by)
    if not examples:
        print("  llm:       (no examples — skipped)")
        return

    # Rebuild the deduped, source-weighted vote exactly as the pipeline does.
    donor_txn = _donor_transactions([m["transaction_id"] for m in similar], conn)
    matches = []
    for m in similar:
        ann = ann_by.get(m["transaction_id"])
        if not ann:
            continue
        dt = donor_txn.get(m["transaction_id"], {})
        matches.append({
            "transaction_id": m["transaction_id"], "distance": m["distance"],
            "category": ann.get("category"), "source": ann.get("source"),
            "annotation": ann, "upi_meta": dt.get("upi_meta"),
            "raw_description": dt.get("raw_description"),
        })
    matches = _dedup_donors(matches)
    example_cats = [m["category"] for m in matches if m.get("category")]
    maj_cat, maj_count = _majority_category(example_cats)
    vote_cat, vote_share, trusted_w = _weighted_trusted_vote(matches)
    print(
        f"  vote:      {vote_cat}  share={vote_share:.2f}  trusted_w={trusted_w:.2f}  "
        f"(floor={settings.rag_consensus_floor})"
    )

    old = annotate_transaction_llm_with_examples(txn, category_list, examples)
    new = annotate_transaction_llm_with_examples(
        txn, category_list, examples, maj_cat, maj_count
    )

    def _fmt(r, apply_caps: bool):
        if r is None:
            return "FAILED"
        eff, flag = r.confidence, ""
        if apply_caps:
            if example_cats and r.category not in set(example_cats):
                eff = min(eff, settings.rag_offexample_confidence_cap)
                flag = f"  [off-example → {eff:.2f} → review]"
            elif (
                trusted_w > 0
                and vote_share < settings.rag_consensus_floor
                and r.confidence < settings.rag_defer_llm_confidence
            ):
                eff = min(eff, settings.rag_defer_confidence_cap)
                flag = f"  [defer (split + unsure) → {eff:.2f} → review]"
        return f"{r.category}/{r.subcategory} conf={r.confidence:.2f}{flag}"

    print(f"  llm OLD:   {_fmt(old, apply_caps=False)}")
    print(f"  llm NEW:   {_fmt(new, apply_caps=True)}")


if __name__ == "__main__":
    raise SystemExit(main())
