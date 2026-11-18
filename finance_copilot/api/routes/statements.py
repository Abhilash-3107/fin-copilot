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
from finance_copilot.db.queries.accounts import (
    NAME_MAX_LENGTH,
    account_labels,
    create_account,
    delete_empty_accounts,
    label_for_statement,
    ref_last4,
)
from finance_copilot.db.queries.annotations import sorted_counts_by_statement
from finance_copilot.db.queries.app_settings import get_confidence_threshold
from finance_copilot.db.queries.embeddings import delete_embeddings
from finance_copilot.db.queries.transactions import (
    delete_payments,
    list_transactions,
    merge_duplicate_payments,
    statement_balance_checks,
)
from finance_copilot.demo_seed import has_demo_data
from finance_copilot.pipeline.dedup import payments_account_already_has
from finance_copilot.pipeline.ingest import (
    DuplicateStatementError,
    NothingNewError,
    check_continuity,
    check_description_formats,
    ingest_pdf,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Bank statements are a few MB at most; refuse anything larger so a bad or hostile
# upload can't exhaust memory (the file is buffered to read it).
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _public(statement: dict) -> dict:
    """A statement row as the API shows it: the account number only as its last
    four digits. The full number is the key to the account; it stays in the ledger."""
    shown = {k: v for k, v in statement.items() if k != "account_ref"}
    shown["account_ref_last4"] = ref_last4(statement.get("account_ref"))
    return shown


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
        except (DuplicateStatementError, NothingNewError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        os.unlink(tmp_path)

    warnings = (
        check_continuity(conn, statement)
        + statement.notices
        + check_description_formats(conn, statement)
    )

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
        **_public(statement.model_dump()),
        "warnings": warnings,
        "transaction_count": transaction_count,
        # What the file held, what of it was new, and what the account had already.
        "rows_in_file": statement.rows_in_file,
        "rows_added": statement.rows_added,
        "rows_already_in": statement.rows_in_file - statement.rows_added,
        "balance_check": balance_check,
        "sorting_job_id": sorting_job_id,
        "account_label": label_for_statement(account_labels(conn), statement.model_dump()),
        "account_is_new": statement.account_is_new,
    }


@router.get("")
def list_statements(conn: sqlite3.Connection = Depends(get_db)):
    """List statements, each enriched with payment and sorted counts and the days and
    months its own payments fall in.

    Counts are computed in grouped queries (no N+1) so the UI can show sorting
    progress per statement. sorted_count means what Teach Me
    means: payments it is not waiting on, so a guess below the review bar is
    not counted.
    """
    rows = conn.execute(
        """
        SELECT
            s.*,
            COUNT(DISTINCT t.id) AS txn_count,
            MIN(t.txn_date) AS first_payment,
            MAX(t.txn_date) AS last_payment,
            GROUP_CONCAT(DISTINCT strftime('%Y-%m', t.txn_date)) AS payment_months
        FROM statements s
        LEFT JOIN transactions t ON t.statement_id = s.id
        GROUP BY s.id
        ORDER BY s.uploaded_at DESC
        """
    ).fetchall()
    # Per-statement running-balance reconciliation for the "N/N rows balanced"
    # trust indicator (4.20). One extra windowed query, merged in by id.
    checks = statement_balance_checks(conn)
    sorted_counts = sorted_counts_by_statement(conn, get_confidence_threshold(conn))
    shared = _shared_with_others(conn)
    labels = account_labels(conn)
    result = []
    for row in rows:
        d = dict(row)
        d["account_label"] = label_for_statement(labels, d)
        d = _public(d)
        # Where this statement's own payments are (it may run across months its
        # payments do not): the first and last day and the months they fall in.
        d["payment_months"] = sorted((d["payment_months"] or "").split(",")) if d["payment_months"] else []
        d["sorted_count"] = sorted_counts.get(d["id"], 0)
        d["balance_check"] = checks.get(d["id"], {"checked": 0, "consistent": 0, "balanced": True})
        d["shared_with_others"] = shared.get(d["id"], 0)
        result.append(d)
    return result


def _shared_with_others(conn: sqlite3.Connection) -> dict[str, int]:
    """{statement id: how many of its payments another statement of the account
    also had}. A statement that skipped payments (rows_added below rows_in_file),
    when it was added or when it was moved into the account, left the other
    statement as their only holder, so deleting that one takes them for good.
    The skipped payments are not stored, so the dates the skipping statement
    covers stand for them: the bank prints every payment of the days it covers."""
    return {
        r["id"]: r["n"]
        for r in conn.execute(
            """
            SELECT s.id AS id, COUNT(DISTINCT t.id) AS n
            FROM statements s
            JOIN transactions t ON t.statement_id = s.id
            JOIN statements o ON o.account_id = s.account_id AND o.id != s.id
                AND o.rows_in_file > o.rows_added
                AND t.txn_date BETWEEN o.period_start AND o.period_end
            GROUP BY s.id
            """
        )
    }


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


class StatementPatch(BaseModel):
    """Where a statement goes: another existing account, or `new_account` to start
    one for it (bank and number taken from the statement), which needs a name."""

    account_id: str | None = None
    new_account: bool = False
    name: str | None = None


@router.patch("/{statement_id}")
def patch_statement(
    statement_id: str,
    body: StatementPatch,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Move a statement to another account, or to a new one (when ingest put it in
    the wrong one, say a statement of a second account that printed no number). An
    account left with no statements goes away."""
    if bool(body.account_id) == body.new_account:
        raise HTTPException(status_code=422, detail="Say which account to move it to, or ask for a new one.")
    name = (body.name or "").strip()
    if body.new_account and not name:
        raise HTTPException(
            status_code=422,
            detail="Give the new account a name, so you can tell the two accounts apart.",
        )
    if len(name) > NAME_MAX_LENGTH:
        raise HTTPException(
            status_code=422, detail=f"An account name can be at most {NAME_MAX_LENGTH} characters."
        )
    with _exclusive(conn):
        statement = conn.execute(
            "SELECT * FROM statements WHERE id = ?", (statement_id,)
        ).fetchone()
        if not statement:
            raise HTTPException(status_code=404, detail="Statement not found")
        if body.new_account:
            siblings = conn.execute(
                "SELECT COUNT(*) FROM statements WHERE account_id = ?", (statement["account_id"],)
            ).fetchone()[0]
            if siblings <= 1:
                raise HTTPException(
                    status_code=422,
                    detail="This is the only statement in its account, so it already has an account of its own.",
                )
            _refuse_while_sorting(conn, statement_id, "Move it")
            target = create_account(conn, statement["bank_name"], statement["account_ref"], name)
        else:
            target = body.account_id
            if not conn.execute("SELECT 1 FROM accounts WHERE id = ?", (target,)).fetchone():
                raise HTTPException(status_code=404, detail="Account not found")
            if statement["account_id"] != target:
                _refuse_while_sorting(conn, statement_id, "Move it")
        already_there = 0
        carried_over = 0
        deleted = False
        if statement["account_id"] != target:
            # The target may hold some of these payments already (the file first
            # landed in the wrong account): the account's own copies stay, the
            # duplicates go (after their work moves over), and a statement left
            # with no payments goes too.
            duplicates = payments_account_already_has(conn, statement_id, target)
            already_there = len(duplicates)
            if duplicates:
                # What the user did to the duplicates goes onto the account's own
                # copies first; the same step then deletes them.
                carried_over = merge_duplicate_payments(conn, duplicates)
                delete_payments(conn, [dup for dup, _ in duplicates])
                conn.execute(
                    "UPDATE statements SET rows_added = MAX(COALESCE(rows_added, 0) - ?, 0) WHERE id = ?",
                    (already_there, statement_id),
                )
            deleted = conn.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM transactions WHERE statement_id = ?)", (statement_id,)
            ).fetchone()[0] == 1
            if deleted:
                conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))
                conn.execute(
                    "DELETE FROM annotation_jobs WHERE statement_id = ? AND status = 'queued' AND kind = 'annotate'",
                    (statement_id,),
                )
            else:
                conn.execute("UPDATE statements SET account_id = ? WHERE id = ?", (target, statement_id))
            delete_empty_accounts(conn)
    return {
        "id": statement_id,
        "account_id": target,
        "account_label": account_labels(conn)[target],
        "rows_already_there": already_there,
        # How many of those had work of the user's (a label, note, name, group or
        # link) that now sits on the payment the account already had.
        "work_carried_over": carried_over,
        "statement_deleted": deleted,
    }


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

        delete_payments(conn, txn_ids)

        conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))
        delete_empty_accounts(conn)
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
