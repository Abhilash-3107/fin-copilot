"""Embedding metadata and vec_items (sqlite-vec) query/upsert helpers."""
from __future__ import annotations

import sqlite3
import struct

import ulid

from src.config import settings


def _serialize_embedding(embedding: list[float]) -> bytes:
    """Pack a list of floats into a compact binary blob for sqlite-vec."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def upsert_embedding(
    conn: sqlite3.Connection,
    transaction_id: str,
    embedding: list[float],
    model_version: str,
) -> None:
    """Insert or replace embedding in vec_items and track in embedding_meta."""
    blob = _serialize_embedding(embedding)
    conn.execute(
        "INSERT OR REPLACE INTO vec_items (transaction_id, embedding) VALUES (?, ?)",
        (transaction_id, blob),
    )
    existing = conn.execute(
        "SELECT id FROM embedding_meta WHERE transaction_id = ?",
        (transaction_id,),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE embedding_meta SET model_version = ?, created_at = datetime('now') WHERE transaction_id = ?",
            (model_version, transaction_id),
        )
    else:
        meta_id = str(ulid.ULID())
        conn.execute(
            "INSERT INTO embedding_meta (id, transaction_id, model_version) VALUES (?, ?, ?)",
            (meta_id, transaction_id, model_version),
        )


def delete_embeddings(conn: sqlite3.Connection, transaction_ids: list[str]) -> int:
    """Delete embedding rows for the given transactions. Returns embedding_meta rows deleted.

    vec_items is a virtual table that only exists when the sqlite-vec extension
    loaded; tolerate its absence so deletes work everywhere.
    """
    if not transaction_ids:
        return 0
    placeholders = ",".join("?" * len(transaction_ids))
    deleted = conn.execute(
        f"DELETE FROM embedding_meta WHERE transaction_id IN ({placeholders})",
        transaction_ids,
    ).rowcount
    try:
        conn.execute(
            f"DELETE FROM vec_items WHERE transaction_id IN ({placeholders})",
            transaction_ids,
        )
    except sqlite3.OperationalError:
        pass  # sqlite-vec not loaded → table never created
    return deleted


def find_similar(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    top_k: int = 5,
    exclude_transaction_ids: list[str] | None = None,
    before_txn_date: str | None = None,
) -> list[dict]:
    """Return top-k most similar transactions by cosine distance.

    Returns list of {transaction_id, distance} dicts.
    sqlite-vec MATCH returns cosine distance — lower means more similar.
    Cosine similarity = 1.0 - distance.

    before_txn_date restricts donors to transactions dated strictly earlier —
    used by the time-split eval harness to prevent retrieval leakage (a replayed
    transaction must never retrieve donors that didn't exist yet).
    """
    blob = _serialize_embedding(query_embedding)
    excluded = set(exclude_transaction_ids or [])
    # Over-fetch because post-MATCH filters drop candidates the vector index
    # already counted toward LIMIT: the model_version filter (always applied)
    # and, in the eval harness, the before_txn_date cutoff. After an embedding
    # model switch mid-re-embed, most nearest neighbours are stale-model rows;
    # a tight fetch would then quietly return 0-2 donors and the pipeline would
    # silently degrade to plain LLM. Widen the fetch whenever any post-filter is
    # in play (i.e. always) so top_k eligible donors survive filtering.
    fetch_limit = max(50, (top_k + len(excluded)) * 10)
    rows = conn.execute(
        "SELECT transaction_id, distance FROM vec_items WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (blob, fetch_limit),
    ).fetchall()

    if not rows:
        return []

    candidate_ids = [row["transaction_id"] for row in rows if row["transaction_id"] not in excluded]
    if not candidate_ids:
        return []

    placeholders = ",".join("?" * len(candidate_ids))
    # The model-version join guards against mixing incompatible vector spaces:
    # vectors embedded by a different model must never match a query vector.
    query = (
        f"SELECT t.id FROM transactions t "
        f"JOIN embedding_meta em ON em.transaction_id = t.id "
        f"WHERE t.id IN ({placeholders}) AND em.model_version = ?"
    )
    params: list = list(candidate_ids) + [settings.ollama_embedding_model]
    if before_txn_date is not None:
        query += " AND t.txn_date < ?"
        params.append(before_txn_date)
    existing_ids = {r[0] for r in conn.execute(query, params).fetchall()}

    results = []
    for row in rows:
        tid = row["transaction_id"]
        if tid not in excluded and tid in existing_ids and len(results) < top_k:
            results.append(dict(row))
    return results


def get_embedding_stats(
    conn: sqlite3.Connection,
    statement_id: str,
) -> dict:
    """Return embedding counts for a statement: total transactions, embedded, annotated."""
    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM transactions WHERE statement_id = ?",
        (statement_id,),
    ).fetchone()["cnt"]

    embedded = conn.execute(
        """SELECT COUNT(*) as cnt FROM embedding_meta em
           JOIN transactions t ON t.id = em.transaction_id
           WHERE t.statement_id = ?""",
        (statement_id,),
    ).fetchone()["cnt"]

    annotated = conn.execute(
        """SELECT COUNT(*) as cnt FROM annotations a
           JOIN transactions t ON t.id = a.transaction_id
           WHERE t.statement_id = ?""",
        (statement_id,),
    ).fetchone()["cnt"]

    return {"total": total, "embedded": embedded, "annotated": annotated}


def get_embedded_transaction_ids(
    conn: sqlite3.Connection,
    statement_id: str | None = None,
) -> set[str]:
    """Return set of transaction IDs that already have embeddings."""
    if statement_id:
        rows = conn.execute(
            """SELECT em.transaction_id FROM embedding_meta em
               JOIN transactions t ON t.id = em.transaction_id
               WHERE t.statement_id = ?""",
            (statement_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT transaction_id FROM embedding_meta").fetchall()
    return {row["transaction_id"] for row in rows}
