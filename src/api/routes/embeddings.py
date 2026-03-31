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


@router.get("/stats/{statement_id}")
def embedding_stats(
    statement_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Return embedding coverage for a statement: total, embedded, annotated counts."""
    return get_embedding_stats(conn, statement_id)
