"""A human label re-decides the machine labels it could change (doc 03).

Retrieval runs on real stored vectors (sqlite-vec), so rag_direct is exercised
end to end; the LLM and the embedding service are wired to explode, proving
re-evaluation never calls either.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from finance_copilot.config import current_embedding_model, settings
from finance_copilot.db.connection import get_connection, init_db
from finance_copilot.db.queries.embeddings import ensure_vec_dimension, upsert_embedding
from finance_copilot.pipeline.annotate import (
    reevaluate_after_teaching,
    reevaluate_machine_labels,
    rescore_review_queue,
)

KEY = "OBEROIFC TUCKSH"


@pytest.fixture(autouse=True)
def _no_network():
    boom = AssertionError("re-evaluation must not call the LLM or the embedding service")
    with patch("finance_copilot.pipeline.annotate.get_embedding_single", side_effect=boom), \
         patch("finance_copilot.pipeline.annotate.annotate_transaction_llm", side_effect=boom), \
         patch("finance_copilot.pipeline.annotate.annotate_transaction_llm_with_examples", side_effect=boom):
        yield


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    init_db(c)
    ensure_vec_dimension(c)
    c.execute("INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s', 't', '1', '2026-06')")
    c.commit()
    return c


def _vec(seed: float) -> list[float]:
    from finance_copilot.config import current_embedding_dim

    v = [0.0] * current_embedding_dim()
    v[0], v[1] = 1.0, seed
    return v


def _row(conn, txn_id, category, source, confidence, *, key=KEY, day=1, vec=None, subcategory=None):
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
        "VALUES (?, 's', ?, 100, 'debit', ?, ?)",
        (txn_id, f"2026-06-{day:02d}", f"UPI/{key}/1234567{day:05d}/UPI", key),
    )
    conn.execute(
        "INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) VALUES (?, ?, ?, ?, ?, ?)",
        (f"a_{txn_id}", txn_id, category, subcategory, confidence, source),
    )
    if vec is not None:
        upsert_embedding(conn, txn_id, vec, current_embedding_model(), "h")
    conn.commit()


def _ann(conn, txn_id):
    return dict(conn.execute("SELECT * FROM annotations WHERE transaction_id = ?", (txn_id,)).fetchone())


class TestPropagation:
    def test_siblings_resolve_once_the_rule_is_established(self, conn):
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 10)
        _row(conn, "q1", "Miscellaneous", "llm", 0.15, day=20)
        changed = reevaluate_after_teaching(conn, ["m2"])
        assert [(c["transaction_id"], c["source"], c["resolved"]) for c in changed] == [("q1", "learned_rule", True)]

    def test_older_siblings_resolve_too(self, conn):
        # Re-evaluation is a production decision about now, not a replay.
        _row(conn, "q1", "Miscellaneous", "llm", 0.15, day=1)
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 10)
        assert [c["transaction_id"] for c in reevaluate_after_teaching(conn, ["m2"])] == ["q1"]

    def test_rag_sibling_resolves_from_its_stored_vector(self, conn):
        _row(conn, "taught", "Food & Dining", "manual", 1.0, key="BLUE TOKAI COF", vec=_vec(0.0))
        _row(conn, "sib", "Miscellaneous", "llm", 0.17, key="BLUE TOKAI COF", day=5, vec=_vec(0.0))
        changed = reevaluate_after_teaching(conn, ["taught"])
        assert changed[0]["source"] == "rag_direct" and changed[0]["resolved"]

    def test_no_stored_vector_never_demotes(self, conn):
        # An embedding outage (or a row never embedded) is not evidence that a
        # rag_direct label lost its donor.
        _row(conn, "m0", "Food & Dining", "manual", 1.0, key="CAFE X")
        _row(conn, "auto", "Food & Dining", "rag_direct", 0.97, key="CAFE X", day=5)
        assert reevaluate_after_teaching(conn, ["m0"]) == []
        assert _ann(conn, "auto")["confidence"] == 0.97

    def test_human_labels_are_never_touched(self, conn):
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1)
        _row(conn, "human_other", "Entertainment", "manual", 1.0, day=9)
        _row(conn, "ur", "Shopping", "user_rule", 0.97, day=10)
        before = {t: _ann(conn, t) for t in ("human_other", "ur")}
        reevaluate_machine_labels(conn, ["human_other", "ur"], reason="teaching")
        assert {t: _ann(conn, t) for t in ("human_other", "ur")} == before

    def test_llm_guess_survives_when_nothing_deterministic_fires(self, conn):
        _row(conn, "m0", "Food & Dining", "manual", 1.0, day=1)  # support 1: no rule yet
        _row(conn, "q1", "Miscellaneous", "llm", 0.15, day=20)
        assert reevaluate_after_teaching(conn, ["m0"]) == []
        assert _ann(conn, "q1")["source"] == "llm"

    def test_rule_loses_purity_and_its_labels_return_to_review(self, conn):
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1)
        _row(conn, "auto", "Food & Dining", "learned_rule", 0.95, day=20, vec=_vec(0.0))
        _row(conn, "m3", "Housing", "manual", 1.0, day=4)
        _row(conn, "m4", "Housing", "manual", 1.0, day=5)
        changed = reevaluate_after_teaching(conn, ["m4"])
        assert [(c["transaction_id"], c["resolved"]) for c in changed] == [("auto", False)]
        assert _ann(conn, "auto")["confidence"] < settings.confidence_threshold

    def test_subcategory_correction_reaches_learned_rule_labels(self, conn):
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1, subcategory="Groceries")
        _row(conn, "auto", "Food & Dining", "learned_rule", 0.95, day=20, subcategory="Groceries")
        conn.execute("UPDATE annotations SET subcategory = 'Restaurants', annotated_at = '2099-01-01' "
                     "WHERE transaction_id = 'm2'")
        conn.commit()
        reevaluate_after_teaching(conn, ["m2"])
        assert _ann(conn, "auto")["subcategory"] == "Restaurants"

    def test_idempotent(self, conn):
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1)
        _row(conn, "q1", "Miscellaneous", "llm", 0.15, day=20)
        reevaluate_after_teaching(conn, ["m2"])
        assert reevaluate_after_teaching(conn, ["m2"]) == []

    def test_rescore_sweeps_the_queue_including_skipped_items(self, conn):
        for i in range(3):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1)
        _row(conn, "q1", "Miscellaneous", "llm", 0.15, day=20)
        conn.execute("UPDATE annotations SET review_snoozed_at = datetime('now') WHERE transaction_id = 'q1'")
        _row(conn, "other", "Miscellaneous", "llm", 0.12, key="SOMEONE ELSE", day=21)
        assert [c["transaction_id"] for c in rescore_review_queue(conn)] == ["q1"]
        assert _ann(conn, "other")["source"] == "llm"


class TestApi:
    @pytest.fixture
    def client(self, conn):
        from fastapi.testclient import TestClient

        from finance_copilot.api.deps import get_db as api_get_db
        from finance_copilot.main import app

        app.dependency_overrides[api_get_db] = lambda: conn
        with patch("finance_copilot.api.routes.annotations.embed_transaction"):
            yield TestClient(app)
        app.dependency_overrides.clear()

    def test_patch_returns_every_reevaluated_row(self, client, conn):
        for i in range(2):
            _row(conn, f"m{i}", "Food & Dining", "manual", 1.0, day=i + 1)
        _row(conn, "teach", "Miscellaneous", "llm", 0.2, day=3)
        _row(conn, "sib", "Miscellaneous", "llm", 0.15, day=20)
        resp = client.patch("/api/annotations/a_teach", json={"category": "Food & Dining"})
        assert resp.status_code == 200
        assert [(r["transaction_id"], r["category"], r["resolved"]) for r in resp.json()["reevaluated"]] == [
            ("sib", "Food & Dining", True)
        ]
