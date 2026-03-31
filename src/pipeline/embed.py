"""Transaction descriptions → embeddings via Ollama; upsert into vec_items and embedding_meta."""
from __future__ import annotations

import json
import sqlite3

import httpx

from src.config import settings
from src.db.queries.embeddings import get_embedded_transaction_ids, upsert_embedding


def build_embed_text(txn: dict) -> str:
    """Build canonical text for embedding: '{debit_credit} {amount} {raw_description} {upi_note}'."""
    upi_note = ""
    upi_meta = txn.get("upi_meta")
    if upi_meta:
        try:
            meta = json.loads(upi_meta) if isinstance(upi_meta, str) else upi_meta
            upi_note = str(meta.get("note") or "")
        except (json.JSONDecodeError, AttributeError):
            pass
    parts = [
        txn.get("debit_credit", ""),
        str(txn.get("amount", "")),
        txn.get("raw_description", ""),
        upi_note,
    ]
    return " ".join(p for p in parts if p).strip()


def get_embeddings_batch(
    texts: list[str],
    timeout: float = 120.0,
) -> list[list[float]]:
    """Call Ollama /api/embed for a batch of texts. Returns list of embedding vectors."""
    url = f"{settings.ollama_url}/api/embed"
    payload = {
        "model": settings.ollama_embedding_model,
        "input": texts,
    }
    response = httpx.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()["embeddings"]


def get_embedding_single(text: str, timeout: float = 60.0) -> list[float]:
    """Get embedding for a single text."""
    return get_embeddings_batch([text], timeout=timeout)[0]


def embed_annotated_transactions(
    conn: sqlite3.Connection,
    statement_id: str | None = None,
    batch_size: int = 32,
) -> dict:
    """Generate embeddings for all annotated transactions that lack embeddings.

    Returns {"embedded": N, "skipped": M} where skipped = already had embeddings.
    Failed batches are skipped so the user can re-run to fill gaps.
    """
    query = """
        SELECT t.* FROM transactions t
        JOIN annotations a ON a.transaction_id = t.id
    """
    params: list = []
    if statement_id:
        query += " WHERE t.statement_id = ?"
        params.append(statement_id)
    query += " ORDER BY t.id"

    rows = conn.execute(query, params).fetchall()
    all_txns = [dict(row) for row in rows]

    already_embedded = get_embedded_transaction_ids(conn, statement_id)
    to_embed = [t for t in all_txns if t["id"] not in already_embedded]

    embedded_count = 0
    model_version = settings.ollama_embedding_model

    for i in range(0, len(to_embed), batch_size):
        batch = to_embed[i : i + batch_size]
        texts = [build_embed_text(txn) for txn in batch]
        try:
            vectors = get_embeddings_batch(texts)
        except (httpx.HTTPError, httpx.TimeoutException):
            continue

        for txn, vec in zip(batch, vectors):
            upsert_embedding(conn, txn["id"], vec, model_version)
            embedded_count += 1

    conn.commit()
    return {"embedded": embedded_count, "skipped": len(already_embedded)}
