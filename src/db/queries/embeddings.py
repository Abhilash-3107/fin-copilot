"""Embedding metadata and vec_items (sqlite-vec) query/upsert helpers."""
from __future__ import annotations

import sqlite3
import struct

import ulid


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


def find_similar(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    top_k: int = 5,
    exclude_transaction_ids: list[str] | None = None,
) -> list[dict]:
    """Return top-k most similar transactions by cosine distance.

    Returns list of {transaction_id, distance} dicts.
    sqlite-vec MATCH returns cosine distance — lower means more similar.
    Cosine similarity = 1.0 - distance.
    """
    blob = _serialize_embedding(query_embedding)
    excluded = set(exclude_transaction_ids or [])
    fetch_limit = top_k + len(excluded)
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
    existing_ids = {
        r[0]
        for r in conn.execute(
            f"SELECT id FROM transactions WHERE id IN ({placeholders})", candidate_ids
        ).fetchall()
    }

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
