"""Local-LLM management routes: status, model download job (resumable, cancelable),
and model removal. The inference itself lives behind the provider seam in
pipeline/llm.py; these routes only manage the artifact."""
from __future__ import annotations

import json
import logging
import sqlite3

import ulid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from finance_copilot import local_llm
from finance_copilot.api.deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/status")
def local_llm_status():
    """Extra/model presence, preflight, manifest, and install hints for Settings."""
    return local_llm.status()


class DownloadRequest(BaseModel):
    model_key: str | None = None


def _run_download_job(job_id: str, model_key: str | None) -> None:
    from finance_copilot.api.routes.annotations import _open_job_connection

    conn = _open_job_connection()
    try:
        conn.execute(
            "UPDATE annotation_jobs SET status='running', updated_at=datetime('now') WHERE id = ?",
            (job_id,),
        )
        conn.commit()

        import time

        last = 0.0

        def progress(done: int, total: int) -> None:
            nonlocal last
            now = time.monotonic()
            if now - last < 1.0 and done < total:
                return  # a 2.7GB download at 1MB chunks would hammer the DB
            last = now
            conn.execute(
                "UPDATE annotation_jobs SET processed=?, total=?, updated_at=datetime('now') WHERE id = ?",
                (done, total, job_id),
            )
            conn.commit()

        path = local_llm.download_model(model_key, progress_cb=progress)
        conn.execute(
            """UPDATE annotation_jobs
               SET status='completed', result=?, updated_at=datetime('now') WHERE id = ?""",
            (json.dumps({"path": str(path)}), job_id),
        )
        conn.commit()
    except Exception as e:
        logger.exception("model download job failed | job=%s", job_id)
        conn.rollback()
        conn.execute(
            "UPDATE annotation_jobs SET status='failed', error=?, updated_at=datetime('now') WHERE id = ?",
            (str(e), job_id),
        )
        conn.commit()
    finally:
        conn.close()


@router.post("/download", status_code=202)
def start_download(
    body: DownloadRequest,
    background_tasks: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Download the local model as a background job (annotation_jobs kind
    'model_download'), with resume + sha256 verification. Disk preflight runs
    up front; a hard RAM shortfall refuses with the BYOM recommendation."""
    key = body.model_key or None
    if key is not None and key not in local_llm.MODEL_MANIFEST:
        raise HTTPException(status_code=422, detail=f"Unknown model '{key}'")
    pf = local_llm.preflight(key)
    if not pf["ok"]:
        raise HTTPException(status_code=409, detail=" ".join(pf["problems"]))

    from finance_copilot.api.routes.annotations import _reap_stale_jobs

    scheduled: str | None = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        _reap_stale_jobs(conn)
        inflight = conn.execute(
            "SELECT id, status, kind FROM annotation_jobs WHERE status IN ('queued','running') "
            "ORDER BY created_at LIMIT 1"
        ).fetchone()
        if inflight is not None:
            conn.commit()
            return {"job_id": inflight["id"], "status": inflight["status"], "kind": inflight["kind"]}
        job_id = str(ulid.ULID())
        conn.execute(
            "INSERT INTO annotation_jobs (id, kind) VALUES (?, 'model_download')",
            (job_id,),
        )
        conn.commit()
        scheduled = job_id
    except Exception:
        conn.rollback()
        raise

    background_tasks.add_task(_run_download_job, scheduled, key)
    return {"job_id": scheduled, "status": "queued", "kind": "model_download"}


@router.post("/download/cancel")
def cancel_download():
    """Signal the in-flight download to stop; the job row resolves to failed.
    The .part file stays on disk so the next attempt resumes from it."""
    local_llm.request_download_cancel()
    return {"cancelled": True}


@router.delete("/model")
def remove_model():
    """Delete the downloaded model (and any partial download)."""
    removed = local_llm.remove_model()
    return {"removed": removed}
