"""A human label re-decides the machine labels it could change (doc 03).

Embeddings are switched off in these tests so the outcomes come from identity
(learned rules) alone; the LLM is wired to explode, proving re-evaluation never
calls it.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from finance_copilot.config import settings
from finance_copilot.db.connection import init_db
from finance_copilot.pipeline.annotate import (
    reevaluate_after_teaching,
    reevaluate_machine_labels,
    rescore_review_queue,
)

KEY = "OBEROIFC TUCKSH"


@pytest.fixture(autouse=True)
def _no_embeddings_no_llm():
    boom = AssertionError("re-evaluation must never call the LLM")
    with patch("finance_copilot.pipeline.annotate.get_embedding_single", side_effect=RuntimeError("off")), \
         patch("finance_copilot.pipeline.annotate.annotate_transaction_llm", side_effect=boom), \
         patch("finance_copilot.pipeline.annotate.annotate_transaction_llm_with_examples", side_effect=boom):
        yield


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s', 't', '1', '2026-06')"
    )
    conn.commit()
    return conn


def _row(conn, txn_id, category, source, confidence, *, key=KEY, day=1, dc="debit"):
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
        "VALUES (?, 's', ?, 100, ?, ?, ?)",
        (txn_id, f"2026-06-{day:02d}", dc, f"UPI/{key}/1234567{day:05d}/UPI", key),
    )
    conn.execute(
        "INSERT INTO annotations (id, transaction_id, category, confidence, source) VALUES (?, ?, ?, ?, ?)",
        (f"a_{txn_id}", txn_id, category, confidence, source),
    )
    conn.commit()


def _ann(conn, txn_id):
    return dict(conn.execute("SELECT * FROM annotations WHERE transaction_id = ?", (txn_id,)).fetchone())


class TestPropagation:
    def test_siblings_resolve_once_the_rule_is_established(self):
        conn = _conn()
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1)
        _row(conn, "q1", "Miscellaneous", "llm", 0.15, day=20)
        _row(conn, "q2", "Miscellaneous", "llm", 0.17, day=21)

        result = reevaluate_after_teaching(conn, ["m2"])

        assert sorted(result["resolved"]) == ["q1", "q2"]
        for t in ("q1", "q2"):
            ann = _ann(conn, t)
            assert ann["source"] == "learned_rule"
            assert ann["category"] == "Food & Dining"
            assert ann["confidence"] >= settings.confidence_threshold

    def test_human_labels_are_never_touched(self):
        conn = _conn()
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1)
        _row(conn, "human_other", "Entertainment", "manual", 1.0, day=9)
        _row(conn, "ur", "Shopping", "user_rule", 0.97, day=10)
        before = {t: _ann(conn, t) for t in ("human_other", "ur")}

        reevaluate_machine_labels(conn, ["human_other", "ur", "m0"], reason="teaching")

        assert {t: _ann(conn, t) for t in ("human_other", "ur")} == before

    def test_llm_guess_survives_when_nothing_deterministic_fires(self):
        conn = _conn()
        _row(conn, "m0", "Food & Dining", "manual", 1.0, day=1)  # support 1: no rule yet
        _row(conn, "q1", "Miscellaneous", "llm", 0.15, day=20)
        result = reevaluate_after_teaching(conn, ["m0"])
        assert result == {"resolved": [], "changed": 0, "demoted": 0}
        assert _ann(conn, "q1")["source"] == "llm"

    def test_rule_loses_purity_and_its_labels_return_to_review(self):
        conn = _conn()
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1)
        _row(conn, "auto", "Food & Dining", "learned_rule", 0.95, day=20)
        # The user now labels the payee differently: purity 3/5 < 0.9.
        _row(conn, "m3", "Housing", "manual", 1.0, day=4)
        _row(conn, "m4", "Housing", "manual", 1.0, day=5)

        result = reevaluate_after_teaching(conn, ["m4"])

        assert result["demoted"] == 1
        assert _ann(conn, "auto")["confidence"] < settings.confidence_threshold

    def test_idempotent(self):
        conn = _conn()
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1)
        _row(conn, "q1", "Miscellaneous", "llm", 0.15, day=20)
        reevaluate_after_teaching(conn, ["m2"])
        assert reevaluate_after_teaching(conn, ["m2"]) == {"resolved": [], "changed": 0, "demoted": 0}

    def test_rescore_sweeps_the_queue(self):
        conn = _conn()
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1)
        _row(conn, "q1", "Miscellaneous", "llm", 0.15, day=20)
        _row(conn, "other", "Miscellaneous", "llm", 0.12, key="SOMEONE ELSE", day=21)
        result = rescore_review_queue(conn)
        assert result["resolved"] == ["q1"]
        assert _ann(conn, "other")["source"] == "llm"

    def test_snooze_survives_when_the_answer_is_unchanged(self):
        conn = _conn()
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1)
        _row(conn, "q1", "Food & Dining", "llm", 0.4, day=20)
        conn.execute("UPDATE annotations SET review_snoozed_at = datetime('now') WHERE transaction_id = 'q1'")
        conn.commit()
        reevaluate_after_teaching(conn, ["m2"])
        ann = _ann(conn, "q1")
        assert ann["source"] == "learned_rule"
        assert ann["review_snoozed_at"] is not None


class TestApi:
    def test_patch_response_lists_resolved_siblings(self):
        from fastapi.testclient import TestClient

        from finance_copilot.api.deps import get_db as api_get_db
        from finance_copilot.main import app

        conn = _conn()
        for i in range(2):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1)
        _row(conn, "teach", "Miscellaneous", "llm", 0.2, day=3)
        _row(conn, "sib", "Miscellaneous", "llm", 0.15, day=20)
        app.dependency_overrides[api_get_db] = lambda: conn
        try:
            with patch("finance_copilot.api.routes.annotations.embed_transaction"):
                resp = TestClient(app).patch("/api/annotations/a_teach", json={"category": "Food & Dining"})
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json()["resolved_transaction_ids"] == ["sib"]
        assert _ann(conn, "sib")["source"] == "learned_rule"
