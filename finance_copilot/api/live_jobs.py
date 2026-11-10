"""Which background jobs have a worker running in this process right now.

The stale-job reaper fails in-flight jobs whose heartbeat went quiet, which is
right for a job whose worker died or never got scheduled, and wrong for a live
worker that is just slow (a cold model load, a long LLM retry, a big model
download). This is a single-process app, so the process itself can say which
jobs are alive: the reaper skips those whatever their heartbeat, and its time
window only has to catch real orphans. A restart empties the set, and startup
fails every job left in flight (reap_interrupted_jobs).
"""
from __future__ import annotations

import functools
import threading

_live: set[str] = set()
_lock = threading.Lock()


def tracks_live_job(worker):
    """Decorate a background worker whose first argument is the job id."""

    @functools.wraps(worker)
    def run(job_id: str, *args, **kwargs):
        with _lock:
            _live.add(job_id)
        try:
            return worker(job_id, *args, **kwargs)
        finally:
            with _lock:
                _live.discard(job_id)

    return run


def live_job_ids() -> list[str]:
    with _lock:
        return sorted(_live)
