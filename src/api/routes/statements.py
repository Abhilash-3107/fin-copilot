"""Statement routes: upload PDF, list statements, list transactions for a statement."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from src.api.deps import get_db
from src.db.queries.embeddings import delete_embeddings
from src.db.queries.transactions import list_transactions
from src.pipeline.ingest import DuplicateStatementError, ingest_pdf

router = APIRouter()


@router.post("/upload")
def upload_statement(
    file: UploadFile,
    # Form body, not query param — query strings end up in server access logs.
    password: Annotated[str | None, Form()] = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Upload a PDF, run the parser, persist statement + transactions, return the Statement."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file.file.read())
        tmp_path = tmp.name

    try:
        try:
            statement = ingest_pdf(tmp_path, password=password, conn=conn)
        except DuplicateStatementError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        os.unlink(tmp_path)

    return statement.model_dump()


@router.get("")
def list_statements(conn: sqlite3.Connection = Depends(get_db)):
    """List statements, each enriched with transaction/annotation/embedding counts.

    Counts are computed in a single grouped query (no N+1) so the UI can show
    annotation and vector-DB coverage per statement.
    """
    rows = conn.execute(
        """
        SELECT
            s.*,
            COUNT(DISTINCT t.id)              AS txn_count,
            COUNT(DISTINCT a.transaction_id)  AS annotated_count,
            COUNT(DISTINCT em.transaction_id) AS embedded_count
        FROM statements s
        LEFT JOIN transactions t   ON t.statement_id = s.id
        LEFT JOIN annotations a    ON a.transaction_id = t.id
        LEFT JOIN embedding_meta em ON em.transaction_id = t.id
        GROUP BY s.id
        ORDER BY s.uploaded_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


@router.delete("/{statement_id}")
def delete_statement(
    statement_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Delete a statement and all associated transactions, annotations, and embeddings."""
    row = conn.execute("SELECT id FROM statements WHERE id = ?", (statement_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Statement not found")

    txn_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM transactions WHERE statement_id = ?", (statement_id,)
        ).fetchall()
    ]

    if txn_ids:
        placeholders = ",".join("?" * len(txn_ids))
        conn.execute(f"DELETE FROM annotations WHERE transaction_id IN ({placeholders})", txn_ids)
        delete_embeddings(conn, txn_ids)
        conn.execute(f"DELETE FROM transactions WHERE id IN ({placeholders})", txn_ids)

    conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))
    conn.commit()
    return {"deleted": statement_id}


@router.delete("/{statement_id}/data")
def reset_statement_data(
    statement_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Delete all transactions, annotations, and embeddings for a statement, but keep the statement record itself."""
    row = conn.execute("SELECT id FROM statements WHERE id = ?", (statement_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Statement not found")

    txn_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM transactions WHERE statement_id = ?", (statement_id,)
        ).fetchall()
    ]

    if txn_ids:
        placeholders = ",".join("?" * len(txn_ids))
        conn.execute(f"DELETE FROM annotations WHERE transaction_id IN ({placeholders})", txn_ids)
        delete_embeddings(conn, txn_ids)

    conn.commit()
    return {"reset": statement_id, "annotations_deleted": len(txn_ids)}


@router.get("/{statement_id}/transactions")
def get_statement_transactions(
    statement_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    return list_transactions(conn, statement_id=statement_id)
