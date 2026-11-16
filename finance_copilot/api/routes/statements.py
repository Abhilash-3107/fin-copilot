"""Statement routes: upload PDF, list statements, list transactions for a statement."""
from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel

from finance_copilot.api.deps import get_db
from finance_copilot.api.routes.annotations import enqueue_annotation_job
from finance_copilot.config import settings
from finance_copilot.db.queries.annotations import sorted_counts_by_statement
from finance_copilot.db.queries.app_settings import get_confidence_threshold
from finance_copilot.db.queries.embeddings import delete_embeddings
from finance_copilot.db.queries.transactions import list_transactions, statement_balance_checks
from finance_copilot.demo_seed import has_demo_data
from finance_copilot.pipeline.ingest import (
    DuplicateStatementError,
    check_continuity,
    check_description_formats,
    ingest_pdf,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Bank statements are a few MB at most; refuse anything larger so a bad or hostile
# upload can't exhaust memory (the file is buffered to read it).
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.post("/upload")
def upload_statement(
    file: UploadFile,
    # Form body, not query param - query strings end up in server access logs.
    password: Annotated[str | None, Form()] = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Upload a PDF, run the parser, persist statement + transactions, return the Statement.

    The statement is sorted without being asked: a job for it is queued here, on
    the server, so an upload by script gets the same treatment as one from the page.
    """
    # Sample data is never mixed with a real statement: its fake rent and
    # salary would sit in every total.
    if has_demo_data(conn):
        raise HTTPException(
            status_code=409,
            detail="Your ledger still has sample data. Remove it first (Settings > Data), "
            "then add your statement so the two don't mix.",
        )
    contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Statement exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )

    # A PDF starts with "%PDF" (some writers put a few bytes of junk before it).
    # Say so up front rather than let the parser report "no transaction table".
    if b"%PDF" not in contents[:1024]:
        raise HTTPException(
            status_code=422,
            detail=f"{file.filename or 'This file'} isn't a PDF. Only PDF statements can be added.",
        )

    # Stage the statement next to the DB with owner-only perms, not in the shared
    # /tmp: this is the app's most sensitive data, and a crash between write and
    # unlink must not leave a bank statement world-readable in a shared temp dir.
    data_dir = Path(settings.db_path).parent
    data_dir.mkdir(parents=True, exist_ok=True)
    # mkstemp creates the file 0600 by default; write via its fd so the bytes
    # never touch a wider-permissioned path.
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf", dir=data_dir)
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(contents)
    except BaseException:
        os.unlink(tmp_path)
        raise

    try:
        try:
            statement = ingest_pdf(tmp_path, password=password, conn=conn)
        except DuplicateStatementError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        os.unlink(tmp_path)

    warnings = check_continuity(conn, statement) + check_description_formats(conn, statement)

    # Sorting starts by itself. Failing to queue it must not fail an upload that
    # already landed: the statement is there and "Sort again" still works.
    try:
        job = enqueue_annotation_job(conn, statement.id, queue=True)
        sorting_job_id = job["job_id"]
    except Exception:
        logger.exception("could not queue sorting for statement %s", statement.id)
        sorting_job_id = None
    # What the page reports for each file: payments added, and whether the
    # running balance reconciles.
    transaction_count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE statement_id = ?", (statement.id,)
    ).fetchone()[0]
    balance_check = statement_balance_checks(conn, statement_id=statement.id).get(
        statement.id, {"checked": 0, "consistent": 0, "balanced": True}
    )
    return {
        **statement.model_dump(),
        "warnings": warnings,
        "transaction_count": transaction_count,
        "balance_check": balance_check,
        "sorting_job_id": sorting_job_id,
    }


@router.get("")
def list_statements(conn: sqlite3.Connection = Depends(get_db)):
    """List statements, each enriched with payment, sorted and embedding counts.

    Counts are computed in grouped queries (no N+1) so the UI can show sorting
    and vector-DB coverage per statement. sorted_count means what Teach Me
    means: payments it is not waiting on, so a guess below the review bar is
    not counted.
    """
    rows = conn.execute(
        """
        SELECT
            s.*,
            COUNT(DISTINCT t.id)              AS txn_count,
            COUNT(DISTINCT em.transaction_id) AS embedded_count
        FROM statements s
        LEFT JOIN transactions t   ON t.statement_id = s.id
        LEFT JOIN embedding_meta em ON em.transaction_id = t.id
        GROUP BY s.id
        ORDER BY s.uploaded_at DESC
        """
    ).fetchall()
    # Per-statement running-balance reconciliation for the "N/N rows balanced"
    # trust indicator (4.20). One extra windowed query, merged in by id.
    checks = statement_balance_checks(conn)
    sorted_counts = sorted_counts_by_statement(conn, get_confidence_threshold(conn))
    result = []
    for row in rows:
        d = dict(row)
        d["sorted_count"] = sorted_counts.get(d["id"], 0)
        d["balance_check"] = checks.get(d["id"], {"checked": 0, "consistent": 0, "balanced": True})
        result.append(d)
    return result


class StatementPatch(BaseModel):
    bank_name: str


@router.patch("/{statement_id}")
def patch_statement(
    statement_id: str,
    body: StatementPatch,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Update mutable fields on a statement (currently: bank_name)."""
    row = conn.execute("SELECT id FROM statements WHERE id = ?", (statement_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Statement not found")
    conn.execute(
        "UPDATE statements SET bank_name = ? WHERE id = ?",
        (body.bank_name.strip(), statement_id),
    )
    conn.commit()
    return {"id": statement_id, "bank_name": body.bank_name.strip()}


@contextlib.contextmanager
def _exclusive(conn: sqlite3.Connection):
    """Take the write lock before looking, commit on success, undo on error.
    A sort claims its job under the same lock, so a delete that passed the
    sorting check cannot have a runner claim the statement before it commits."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _refuse_while_sorting(conn: sqlite3.Connection, statement_id: str, action: str) -> None:
    """409 while a sort that could be writing this statement's payments is
    running: its own, or one over the whole ledger. A sort still waiting for its
    turn is not a reason to refuse; it is dropped with the statement. Call it
    inside _exclusive."""
    from finance_copilot.api.routes.annotations import _reap_stale_jobs

    _reap_stale_jobs(conn)  # inside the caller's write transaction
    sorting = conn.execute(
        "SELECT 1 FROM annotation_jobs WHERE status = 'running' AND kind = 'annotate' "
        "AND (statement_id = ? OR statement_id IS NULL) LIMIT 1",
        (statement_id,),
    ).fetchone()
    if sorting:
        raise HTTPException(
            status_code=409,
            detail=f"This statement is being sorted right now. {action} once sorting finishes.",
        )


@router.delete("/{statement_id}")
def delete_statement(
    statement_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Delete a statement and all associated transactions, annotations, and embeddings."""
    with _exclusive(conn):
        row = conn.execute("SELECT id FROM statements WHERE id = ?", (statement_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Statement not found")
        _refuse_while_sorting(conn, statement_id, "Delete it")

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
            # transaction_links references transactions(id) without ON DELETE CASCADE,
            # so links must go before the transactions or the FK check (PRAGMA
            # foreign_keys=ON) aborts the delete. A link can cross a statement
            # boundary, so match on either endpoint.
            conn.execute(
                f"DELETE FROM transaction_links "
                f"WHERE txn_a IN ({placeholders}) OR txn_b IN ({placeholders})",
                txn_ids + txn_ids,
            )
            conn.execute(f"DELETE FROM transactions WHERE id IN ({placeholders})", txn_ids)

        conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))
        # Its sort that was still waiting has nothing left to sort.
        conn.execute(
            "DELETE FROM annotation_jobs WHERE statement_id = ? AND status = 'queued' AND kind = 'annotate'",
            (statement_id,),
        )
    return {"deleted": statement_id}


@router.delete("/{statement_id}/data")
def reset_statement_data(
    statement_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Start over with a statement: remove every category and tag on its payments (and what was matched from them), keeping the statement and its payments."""
    with _exclusive(conn):
        row = conn.execute("SELECT id FROM statements WHERE id = ?", (statement_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Statement not found")
        _refuse_while_sorting(conn, statement_id, "Start over")

        txn_ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM transactions WHERE statement_id = ?", (statement_id,)
            ).fetchall()
        ]

        annotations_deleted = 0
        if txn_ids:
            placeholders = ",".join("?" * len(txn_ids))
            cur = conn.execute(
                f"DELETE FROM annotations WHERE transaction_id IN ({placeholders})", txn_ids
            )
            annotations_deleted = cur.rowcount
            delete_embeddings(conn, txn_ids)

    return {"reset": statement_id, "annotations_deleted": annotations_deleted}


@router.get("/{statement_id}/transactions")
def get_statement_transactions(
    statement_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    return list_transactions(conn, statement_id=statement_id)
