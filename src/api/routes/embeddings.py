"""Embedding routes: generate embeddings for annotated transactions, get per-statement stats."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.deps import get_db
from src.db.queries.embeddings import get_embedding_stats
from src.pipeline.embed import embed_annotated_transactions

router = APIRouter()


class EmbedRequest(BaseModel):
    statement_id: str | None = None


class EmbedResponse(BaseModel):
    embedded: int
    skipped: int


@router.post("/generate", response_model=EmbedResponse)
def generate_embeddings(
    body: EmbedRequest,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Generate embeddings for all annotated transactions that lack them.

    Scoped to a statement if statement_id is provided, otherwise all statements.
    Returns counts of newly embedded and already-skipped transactions.
    """
    result = embed_annotated_transactions(conn, body.statement_id)
    return result


@router.delete("/statement/{statement_id}")
def clear_embeddings(
    statement_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Delete all embedding vectors for a statement so they can be regenerated."""
    txn_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM transactions WHERE statement_id = ?", (statement_id,)
        ).fetchall()
    ]
    deleted = 0
    if txn_ids:
        placeholders = ",".join("?" * len(txn_ids))
        deleted = conn.execute(
            f"DELETE FROM embedding_meta WHERE transaction_id IN ({placeholders})", txn_ids
        ).rowcount
        conn.execute(
            f"DELETE FROM vec_items WHERE transaction_id IN ({placeholders})", txn_ids
        )
        conn.commit()
    return {"cleared": deleted}


@router.get("/stats/{statement_id}")
def embedding_stats(
    statement_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Return embedding coverage for a statement: total, embedded, annotated counts."""
    return get_embedding_stats(conn, statement_id)
