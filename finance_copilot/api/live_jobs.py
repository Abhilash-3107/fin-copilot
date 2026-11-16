"""Which background jobs have a worker running in this process right now, and
the one runner thread that takes queued sorting jobs in turn.

The stale-job reaper fails in-flight jobs whose heartbeat went quiet, which is
right for a job whose worker died or never got scheduled, and wrong for a live
worker that is just slow (a cold model load, a long LLM retry, a big model
download). This is a single-process app, so the process itself can say which
jobs are alive: the reaper skips those whatever their heartbeat, and its time
window only has to catch real orphans. A restart empties the set, and startup
fails every job left running (reap_interrupted_jobs).

Sorting jobs wait in the database, not in threads. A statement that lands
inserts a `queued` row and wakes the JobRunner, which takes the oldest queued
row, runs it, and loops. One thread serves any number of queued statements, so
a long queue never holds request threads. While the runner is alive every
queued sorting row counts as live for the reaper: it is waiting for its turn,
not orphaned.
"""
from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

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


# How long the runner sleeps when it has nothing to do or is held back by
# another kind of job. A wake-up from an upload cuts the sleep short; the
# timeout only covers rows nobody signalled for and a held-back runner.
RUNNER_POLL_SECONDS = 2.0


class JobRunner:
    """One thread that runs queued jobs one at a time, oldest first.

    `run_next` runs a single queued job and returns False when there was nothing
    it could run. It must not raise for a job's own failure (the job records
    that on its row); anything it does raise is logged and the loop carries on.
    """

    def __init__(self, run_next: Callable[[], bool], poll_seconds: float = RUNNER_POLL_SECONDS):
        self._run_next = run_next
        self._poll_seconds = poll_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the thread once; a second call while it runs does nothing."""
        if self.alive:
            return
        self._stop.clear()
        self._wake.set()  # pick up rows left queued before this start
        self._thread = threading.Thread(target=self._loop, name="job-runner", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the thread to finish; a job still running past `timeout` is left
        to die with the process (its row is failed at the next startup)."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None

    def wake(self) -> None:
        """A job was queued: look for work now instead of at the next poll."""
        self._wake.set()

    def run_one(self) -> bool:
        """Run the next queued job on the calling thread; False when none ran."""
        try:
            return bool(self._run_next())
        except Exception:
            logger.exception("job runner could not run the next queued job")
            return False

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Clear before looking, so a wake-up that lands while a job runs is
            # seen on the next pass instead of lost.
            self._wake.clear()
            if self.run_one():
                continue
            self._wake.wait(self._poll_seconds)
