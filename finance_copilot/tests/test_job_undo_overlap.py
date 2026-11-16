"""A sorting job (its own connection, retries on a busy database) and a Teach Me
undo (BEGIN IMMEDIATE on the request's connection) running at the same time:
neither may fail and neither may corrupt the other's rows."""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from finance_copilot.pipeline.llm import AnnotationResponse


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A file-backed ledger (the job opens its own connection): an older
    statement with two guessed payments Teach Me can answer, and a new one
    with ten rule matches and two the model has to decide."""
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.config import settings
    from finance_copilot.db import connection as dbc
    from finance_copilot.main import app

    db_path = str(tmp_path / "overlap.db")
    monkeypatch.setattr(settings, "db_path", db_path)
    conn = dbc.get_migrated_db(db_path)

    def override():
        c = dbc.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[api_get_db] = override
    for sid, month in (("old", "2026-05"), ("new", "2026-06")):
        conn.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES (?, 'test', '1', ?)",
            (sid, month),
        )

    def pay(tid, sid, desc, key=None):
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
            "VALUES (?, ?, '2026-06-01', 199.0, 'debit', ?, ?)",
            (tid, sid, desc, key),
        )

    for n in range(1, 5):
        pay(f"g{n}", "old", f"UPI/CHAI WALA {n}/12345678{n:04d}/UPI", f"chai wala {n}")
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, confidence, source) "
            "VALUES (?, ?, 'Shopping', 0.4, 'llm')",
            (f"ga{n}", f"g{n}"),
        )
    for n in range(10):
        pay(f"r{n}", "new", "Netflix subscription")
    for n in range(2):
        pay(f"u{n}", "new", f"zqx unknown vendor {n}")
    conn.commit()
    yield TestClient(app), conn
    app.dependency_overrides.pop(api_get_db, None)
    conn.close()


def _state(conn, ids):
    marks = ",".join("?" * len(ids))
    return {
        "annotations": [
            dict(r) for r in conn.execute(
                f"SELECT * FROM annotations WHERE transaction_id IN ({marks}) ORDER BY transaction_id", ids
            )
        ],
        "feedback": [tuple(r) for r in conn.execute("SELECT * FROM feedback_stats ORDER BY source, category")],
    }


def _answer(client, ids, category="Food & Dining"):
    resp = client.post(
        "/api/annotations/bulk-label", json={"transaction_ids": ids, "label": {"category": category}}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["undo_token"]


def _undo(client, token):
    return client.post("/api/annotations/review-queue/undo", json={"token": token})


def _job(client, job_id):
    return client.get(f"/api/annotations/jobs/{job_id}").json()


def test_an_undo_waits_out_the_jobs_write_and_both_finish(ledger):
    """The job holds an uncommitted batch of rule matches (the write lock) while
    the undo asks for BEGIN IMMEDIATE on another connection: the undo waits for
    the batch to commit, then restores exactly, and the job still completes."""
    from finance_copilot.pipeline import annotate

    client, conn = ledger
    ids = ["g1", "g2"]
    before = _state(conn, ids)
    token = _answer(client, ids)
    assert _state(conn, ids) != before

    undone: dict = {}

    def undo_in_thread():
        undone["resp"] = _undo(client, token)

    thread = threading.Thread(target=undo_in_thread)
    real_rule = annotate.rule_annotation
    calls = {"n": 0}

    def rule_then_overlap(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 6:  # the first matches are written and not yet committed (progress commits are throttled)
            thread.start()
            time.sleep(0.4)  # long enough for the undo to be blocked on the lock
            resp = undone.get("resp")
            assert thread.is_alive(), f"undo returned early: {resp and (resp.status_code, resp.text)}"
        return real_rule(*args, **kwargs)

    with patch("finance_copilot.pipeline.annotate.rule_annotation", side_effect=rule_then_overlap), \
         patch("finance_copilot.pipeline.annotate.get_embedding_single", side_effect=Exception("no embedder")), \
         patch(
             "finance_copilot.pipeline.annotate.annotate_transaction_llm",
             return_value=AnnotationResponse(category="Shopping", confidence=0.7),
         ):
        job_id = client.post("/api/annotations/auto-annotate/jobs", json={"statement_id": "new"}).json()["job_id"]
    thread.join(10)
    assert not thread.is_alive()

    assert undone["resp"].status_code == 200, undone["resp"].text
    assert _state(conn, ids) == before
    job = _job(client, job_id)
    assert job["status"] == "completed", job
    assert job["result"]["rule_matched"] == 10
    assert job["result"]["llm_annotated"] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM annotations a JOIN transactions t ON t.id = a.transaction_id WHERE t.statement_id = 'new'"
    ).fetchone()[0] == 12
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_answers_and_undos_while_the_job_waits_on_the_model(ledger):
    """The job releases the write lock before each model call, so a Teach Me
    answer and an undo made during one go straight through, and the job then
    finishes the rows it was on."""
    client, conn = ledger
    answered: list = []

    def llm(_txn, _cats):
        if not answered:
            token = _answer(client, ["g3", "g4"], "Health")
            answered.append(token)
            assert _undo(client, token).status_code == 200
        return AnnotationResponse(category="Shopping", confidence=0.7)

    before = _state(conn, ["g3", "g4"])
    with patch("finance_copilot.pipeline.annotate.get_embedding_single", side_effect=Exception("no embedder")), \
         patch("finance_copilot.pipeline.annotate.annotate_transaction_llm", side_effect=llm):
        job_id = client.post("/api/annotations/auto-annotate/jobs", json={"statement_id": "new"}).json()["job_id"]

    assert answered, "the model stand-in should have been called"
    job = _job(client, job_id)
    assert job["status"] == "completed", job
    assert job["result"]["llm_annotated"] == 2
    assert _state(conn, ["g3", "g4"]) == before
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
