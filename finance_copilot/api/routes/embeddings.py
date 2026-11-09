"""Embedding routes: generate embeddings for annotated transactions, get per-statement stats,
and the full re-embed background job used after an embedding-provider switch."""
from __future__ import annotations

import json
import logging
import sqlite3

import ulid
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from finance_copilot.api.deps import get_db
from finance_copilot.db.queries.embeddings import (
    delete_embeddings,
    ensure_vec_dimension,
    get_embedding_stats,
)
from finance_copilot.pipeline.annotate import rescore_review_queue
from finance_copilot.pipeline.embed import refresh_embeddings, stale_embedding_rows

logger = logging.getLogger(__name__)

router = APIRouter()


class EmbedRequest(BaseModel):
    statement_id: str | None = None


class EmbedResponse(BaseModel):
    embedded: int


@router.post("/generate", response_model=EmbedResponse)
def generate_embeddings(
    body: EmbedRequest,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Embed every annotated transaction whose vector is missing or stale.

    Scoped to a statement if statement_id is provided, otherwise all statements.
    """
    return refresh_embeddings(conn, body.statement_id)


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
    deleted = delete_embeddings(conn, txn_ids)
    conn.commit()
    return {"cleared": deleted}


def _run_reembed_job(job_id: str) -> None:
    # Import here to avoid a routes-module import cycle; annotations owns the
    # shared job-connection/heartbeat helpers.
    from finance_copilot.api.routes.annotations import _open_job_connection

    conn = _open_job_connection()
    try:
        conn.execute(
            "UPDATE annotation_jobs SET status='running', updated_at=datetime('now') WHERE id = ?",
            (job_id,),
        )
        conn.commit()

        # A dimension change (e.g. nomic 768 → bge-small 384) rebuilds vec_items
        # before any vector is written.
        ensure_vec_dimension(conn)

        def progress(processed: int, total: int) -> None:
            conn.execute(
                "UPDATE annotation_jobs SET processed=?, total=?, updated_at=datetime('now') WHERE id = ?",
                (processed, total, job_id),
            )
            conn.commit()

        result = refresh_embeddings(conn, progress_cb=progress)
        # Fresh vectors (new model or embed text) can settle queued items.
        rescore_review_queue(conn)
        conn.execute(
            """UPDATE annotation_jobs
               SET status='completed', processed=?, total=?, result=?, updated_at=datetime('now')
               WHERE id = ?""",
            (result["embedded"], result["embedded"], json.dumps(result), job_id),
        )
        conn.commit()
    except Exception as e:
        logger.exception("reembed job failed | job=%s", job_id)
        conn.rollback()
        conn.execute(
            "UPDATE annotation_jobs SET status='failed', error=?, updated_at=datetime('now') WHERE id = ?",
            (str(e), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def enqueue_reembed_job(conn: sqlite3.Connection) -> tuple[dict | None, str | None]:
    """Insert a queued 'reembed' job under the single-job guard.

    Returns (inflight_job, None) when another job already holds the guard, else
    (None, new_job_id); the caller runs _run_reembed_job(new_job_id).
    """
    from finance_copilot.api.routes.annotations import _reap_stale_jobs

    conn.execute("BEGIN IMMEDIATE")
    try:
        _reap_stale_jobs(conn)
        inflight = conn.execute(
            "SELECT id, status, kind FROM annotation_jobs WHERE status IN ('queued','running') "
            "ORDER BY created_at LIMIT 1"
        ).fetchone()
        if inflight is not None:
            conn.commit()
            return dict(inflight), None
        job_id = str(ulid.ULID())
        conn.execute("INSERT INTO annotation_jobs (id, kind) VALUES (?, 'reembed')", (job_id,))
        conn.commit()
        return None, job_id
    except Exception:
        conn.rollback()
        raise


def refresh_stale_embeddings_in_background() -> None:
    """Startup: rebuild vectors that are missing, from another model, or built
    from text that has since changed, on a daemon thread. Finding the stale rows
    builds every donor's embed text, so it stays off the startup path too."""
    import threading

    def run() -> None:
        from finance_copilot.api.routes.annotations import _open_job_connection

        conn = _open_job_connection()
        try:
            stale = len(stale_embedding_rows(conn))
            if not stale:
                return
            _inflight, job_id = enqueue_reembed_job(conn)
        finally:
            conn.close()
        if job_id is not None:
            logger.info("%d stored vectors are stale - rebuilding them (job %s)", stale, job_id)
            _run_reembed_job(job_id)

    threading.Thread(target=run, daemon=True, name="embedding-refresh").start()


@router.post("/reembed", status_code=202)
def start_reembed_job(
    background_tasks: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Rebuild the whole vector memory with the current embedding provider.

    Runs as a background job in the shared annotation_jobs table (kind
    'reembed') under the same single-job guard: RAG retrieval is meaningless
    mid-rebuild, so annotation must wait its turn. Poll
    GET /api/annotations/jobs/{id} for progress.
    """
    inflight, scheduled_job_id = enqueue_reembed_job(conn)
    if inflight is not None:
        return {"job_id": inflight["id"], "status": inflight["status"], "kind": inflight["kind"]}

    background_tasks.add_task(_run_reembed_job, scheduled_job_id)
    return {"job_id": scheduled_job_id, "status": "queued", "kind": "reembed"}


@router.get("/stats/{statement_id}")
def embedding_stats(
    statement_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Return embedding coverage for a statement: total, embedded, annotated counts."""
    return get_embedding_stats(conn, statement_id)
