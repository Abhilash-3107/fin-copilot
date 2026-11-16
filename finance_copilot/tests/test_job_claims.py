"""A sorting job is claimed once, finishes only if it is still the running job,
survives a restart, and cannot be deleted from under its worker."""
from __future__ import annotations

import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from finance_copilot.config import settings
from finance_copilot.db import connection as dbc


@pytest.fixture
def ledger(monkeypatch):
    """A migrated on-disk ledger in the test's temp data folder, served by the app."""
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.main import app

    monkeypatch.setattr(settings, "llm_provider", "none")
    conn = dbc.get_migrated_db(settings.db_path)

    def override():
        c = dbc.get_connection(settings.db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[api_get_db] = override
    yield TestClient(app), conn
    app.dependency_overrides.pop(api_get_db, None)
    conn.close()


def _statement(conn, statement_id, txn_id=None):
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES (?, 'kotak', '1', '2026-01')",
        (statement_id,),
    )
    if txn_id:
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
            "VALUES (?, ?, '2026-01-15', 10.0, 'debit', 'SOMEWHERE')",
            (txn_id, statement_id),
        )
    conn.commit()


def _job(conn, job_id, status="queued", statement_id=None, kind="annotate", created_at="2026-01-01 00:00:00"):
    conn.execute(
        "INSERT INTO annotation_jobs (id, status, kind, statement_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (job_id, status, kind, statement_id, created_at),
    )
    conn.commit()


def _status(conn, job_id):
    return conn.execute("SELECT status FROM annotation_jobs WHERE id = ?", (job_id,)).fetchone()[0]


class TestClaim:
    def test_two_runners_never_run_the_same_job(self, ledger):
        from finance_copilot.api.routes.annotations import _run_next_queued_job

        _, conn = ledger
        _job(conn, "only")
        ran = []
        barrier = threading.Barrier(2)

        def worker(job_id, statement_id, transaction_ids):
            ran.append(job_id)

        def runner_pass():
            barrier.wait()
            _run_next_queued_job()

        with patch("finance_copilot.api.routes.annotations._run_annotation_job", worker):
            threads = [threading.Thread(target=runner_pass) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        assert ran == ["only"]
        assert _status(conn, "only") == "running"

    def test_a_claimed_job_is_running_before_its_worker_starts(self, ledger):
        from finance_copilot.api.routes.annotations import _run_next_queued_job

        _, conn = ledger
        _job(conn, "j")
        seen = []

        def worker(job_id, statement_id, transaction_ids):
            seen.append(_status(conn, job_id))

        with patch("finance_copilot.api.routes.annotations._run_annotation_job", worker):
            assert _run_next_queued_job() is True
        assert seen == ["running"]


class TestFinish:
    def _run(self, conn, during):
        from finance_copilot.api.routes.annotations import _run_annotation_job
        from finance_copilot.models.annotation import AutoAnnotateResult

        _job(conn, "w", status="running")

        def fake_auto_annotate(c, statement_id, transaction_ids, progress_cb=None):
            during()
            return AutoAnnotateResult(
                total_processed=0, rule_matched=0, llm_annotated=0, llm_failed=0,
                low_confidence=0, already_annotated=0,
            )

        with patch("finance_copilot.api.routes.annotations.auto_annotate", fake_auto_annotate):
            _run_annotation_job("w", None, None)

    def test_a_job_failed_meanwhile_is_not_marked_completed(self, ledger):
        _, conn = ledger

        def reaped_meanwhile():
            c = dbc.get_connection(settings.db_path)
            c.execute("UPDATE annotation_jobs SET status='failed', error='Went stale without a worker' WHERE id='w'")
            c.commit()
            c.close()

        self._run(conn, reaped_meanwhile)
        row = conn.execute("SELECT status, error, result FROM annotation_jobs WHERE id='w'").fetchone()
        assert row["status"] == "failed"
        assert row["result"] is None

    def test_a_job_that_finishes_normally_is_completed(self, ledger):
        _, conn = ledger
        self._run(conn, lambda: None)
        assert _status(conn, "w") == "completed"


class TestRunnerRestart:
    def test_stopping_a_runner_stuck_in_a_job_and_starting_again_leaves_one_thread(self):
        from finance_copilot.api.live_jobs import JobRunner

        entered = threading.Event()
        release = threading.Event()
        calls = []

        def run_next():
            calls.append(threading.current_thread().name)
            entered.set()
            release.wait(10)
            return False

        runner = JobRunner(run_next, poll_seconds=0.02)
        runner.start()
        assert entered.wait(5)
        runner.stop(timeout=0.05)  # the job is still running, so the thread outlives the stop
        first = [t for t in threading.enumerate() if t.name == "job-runner"]
        assert len(first) == 1
        runner.start()
        release.set()
        first[0].join(5)
        try:
            # The old thread honoured its own stop and left; only the new one remains.
            assert not first[0].is_alive()
            assert runner.alive
        finally:
            runner.stop()


class TestStartup:
    def test_a_sort_interrupted_by_a_restart_goes_back_in_line(self, ledger):
        from finance_copilot.api.routes.annotations import reap_interrupted_jobs

        _, conn = ledger
        _job(conn, "sort", status="running")
        conn.execute("UPDATE annotation_jobs SET processed = 4, total = 10 WHERE id = 'sort'")
        _job(conn, "embed", status="running", kind="reembed")
        _job(conn, "download", status="queued", kind="model_download")
        conn.commit()

        assert reap_interrupted_jobs(conn) == 2
        sort = conn.execute("SELECT status, processed, total, error FROM annotation_jobs WHERE id='sort'").fetchone()
        assert (sort["status"], sort["processed"], sort["total"], sort["error"]) == ("queued", 0, 0, None)
        assert _status(conn, "embed") == "failed"
        assert _status(conn, "download") == "failed"


    def test_a_sort_that_keeps_being_interrupted_is_failed_not_requeued_forever(self, ledger, caplog):
        """The app closing while it sorts (a crash loop, say) must not put the
        same job back in line for ever: the second interruption ends it."""
        from finance_copilot.api.routes.annotations import (
            _run_next_queued_job,
            reap_interrupted_jobs,
        )

        _, conn = ledger
        _statement(conn, "s1", "t1")
        _job(conn, "sort", statement_id="s1")
        with patch("finance_copilot.api.routes.annotations._run_annotation_job", lambda *a: None):
            assert _run_next_queued_job() is True  # claim 1: the app closes while it runs
            assert reap_interrupted_jobs(conn) == 0
            assert _status(conn, "sort") == "queued"
            assert _run_next_queued_job() is True  # claim 2: closes again
            with caplog.at_level("INFO"):
                assert reap_interrupted_jobs(conn) == 1
        row = conn.execute("SELECT status, error, attempts FROM annotation_jobs WHERE id='sort'").fetchone()
        assert (row["status"], row["attempts"]) == ("failed", 2)
        assert row["error"] == "Stopped: the app closed twice while sorting this statement. Use Sort again."
        assert "requeued 0" in caplog.text and "failed 1" in caplog.text

    def test_a_failed_interrupted_sort_no_longer_blocks_delete_reset_or_restore(self, ledger):
        from finance_copilot.api.routes.annotations import (
            _run_next_queued_job,
            reap_interrupted_jobs,
        )

        client, conn = ledger
        _statement(conn, "s1", "t1")
        backup = client.get("/api/data/backup").content
        _job(conn, "sort", statement_id="s1")
        with patch("finance_copilot.api.routes.annotations._run_annotation_job", lambda *a: None):
            for _ in range(2):
                _run_next_queued_job()
                reap_interrupted_jobs(conn)
        assert _status(conn, "sort") == "failed"
        assert client.delete("/api/statements/s1/data").status_code == 200
        assert client.post("/api/data/delete-all").status_code == 200
        assert client.delete("/api/statements/s1").status_code == 404  # gone, not refused
        resp = client.post("/api/data/restore", files={"file": ("backup.db", backup, "application/x-sqlite3")})
        assert resp.status_code == 200


    def test_a_queued_sort_for_a_statement_the_ledger_lacks_completes_with_nothing_to_do(self, ledger):
        """After a restore the queue can name a statement that is not in the
        restored ledger: it finds nothing to sort and ends, it does not wedge the line."""
        from finance_copilot.api.routes.annotations import runner

        _, conn = ledger
        _job(conn, "ghost", statement_id="not-in-this-ledger")
        assert runner.run_one() is True
        assert _status(conn, "ghost") == "completed"


class TestDeleteWhileSorting:
    def test_delete_everything_is_refused_while_a_job_is_waiting_or_running(self, ledger):
        client, conn = ledger
        _statement(conn, "s1", "t1")
        _job(conn, "j", status="running", statement_id="s1")

        resp = client.post("/api/data/delete-all")
        assert resp.status_code == 409
        assert "once it finishes" in resp.json()["detail"]
        assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 1

    def test_deleting_a_statement_is_refused_while_its_sort_runs(self, ledger):
        client, conn = ledger
        _statement(conn, "s1", "t1")
        _job(conn, "j", status="running", statement_id="s1")

        resp = client.delete("/api/statements/s1")
        assert resp.status_code == 409
        assert "sorting" in resp.json()["detail"].lower()
        assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 1

    def test_deleting_a_statement_removes_its_waiting_sort_and_keeps_the_others(self, ledger):
        client, conn = ledger
        _statement(conn, "s1", "t1")
        _statement(conn, "s2", "t2")
        _job(conn, "waiting1", statement_id="s1")
        _job(conn, "waiting2", statement_id="s2", created_at="2026-01-01 00:00:01")

        assert client.delete("/api/statements/s1").status_code == 200
        ids = {row[0] for row in conn.execute("SELECT id FROM annotation_jobs")}
        assert ids == {"waiting2"}

    def test_another_statements_running_sort_does_not_block_a_delete(self, ledger):
        client, conn = ledger
        _statement(conn, "s1", "t1")
        _statement(conn, "s2", "t2")
        _job(conn, "j", status="running", statement_id="s2")

        assert client.delete("/api/statements/s1").status_code == 200

    def test_a_whole_ledger_sort_blocks_every_delete(self, ledger):
        client, conn = ledger
        _statement(conn, "s1", "t1")
        _job(conn, "j", status="running", statement_id=None)

        assert client.delete("/api/statements/s1").status_code == 409


class TestDeleteRacesTheClaim:
    """The guard looks, then the delete writes. A runner claiming the statement's
    waiting sort in between would sort payments that are being deleted."""

    @pytest.mark.parametrize("call", [
        lambda client: client.delete("/api/statements/s1"),
        lambda client: client.delete("/api/statements/s1/data"),
    ], ids=["delete", "reset"])
    def test_a_claim_waits_for_the_delete_that_passed_the_guard(self, ledger, call):
        from finance_copilot.api.routes import statements
        from finance_copilot.api.routes.annotations import _run_next_queued_job

        client, conn = ledger
        _statement(conn, "s1", "t1")
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, confidence, source) "
            "VALUES ('a1', 't1', 'Shopping', 0.9, 'manual')"
        )
        conn.commit()
        _job(conn, "sort", statement_id="s1")
        labels_when_sorted = []  # what the claimed sort would find already labelled
        claimer = threading.Thread(
            target=lambda: _run_next_queued_job(), name="claimer")

        real_guard = statements._refuse_while_sorting

        def guard_then_a_runner_tries_to_claim(c, statement_id, action):
            real_guard(c, statement_id, action)
            # Past the guard, before the delete writes: a runner wakes up right now.
            claimer.start()
            claimer.join(0.5)

        with patch("finance_copilot.api.routes.annotations._run_annotation_job",
                   lambda job_id, *a: labels_when_sorted.append(
                       conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0])), \
                patch.object(statements, "_refuse_while_sorting", guard_then_a_runner_tries_to_claim):
            assert call(client).status_code == 200
            claimer.join(10)
        # A reset keeps the waiting sort, so it may run afterwards, on nothing; a
        # delete drops it. Either way it never starts on labels being deleted.
        assert labels_when_sorted in ([], [0])


class TestReattachPrefersTheRunningJob:
    """With a queue behind a running job, a caller must be handed the job doing
    the work, never the oldest waiting row."""

    @staticmethod
    def _line(conn):
        _job(conn, "waiting", status="queued", created_at="2026-01-01 00:00:00")
        _job(conn, "working", status="running", created_at="2026-01-01 00:00:05")

    def test_a_reembed_request_attaches_to_the_running_job(self, ledger):
        from finance_copilot.api.routes.embeddings import enqueue_reembed_job

        _, conn = ledger
        self._line(conn)
        inflight, new_id = enqueue_reembed_job(conn)
        assert new_id is None
        assert inflight["id"] == "working"

    def test_a_model_download_request_attaches_to_the_running_job(self, ledger):
        client, conn = ledger
        self._line(conn)
        with patch("finance_copilot.local_llm.preflight", return_value={"ok": True, "problems": []}):
            resp = client.post("/api/local-llm/download", json={})
        assert resp.json()["job_id"] == "working"
