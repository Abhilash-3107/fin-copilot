"""Settings > AI & Model > Advanced: how many payments are ready to match, and
the one "Update now" action that gets the rest ready.

Coverage counts SORTED payments (the same definition Teach Me uses), because
those are the ones that serve as examples for matching. Update now is refused
with a plain reason when matching is off or a sort is running.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from finance_copilot.config import current_embedding_model, settings
from finance_copilot.db.connection import get_connection, init_db


@pytest.fixture
def client_conn(tmp_path):
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.main import app

    conn = get_connection(str(tmp_path / "t.db"))
    init_db(conn)
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s', 't', '1', '2026-06')"
    )
    conn.commit()
    app.dependency_overrides[api_get_db] = lambda: conn
    yield TestClient(app), conn
    app.dependency_overrides.pop(api_get_db, None)
    conn.close()


def _payment(conn, txn_id, *, label="Shopping", source="manual", confidence=1.0, embedded=False):
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
        "VALUES (?, 's', '2026-06-10', 100.0, 'debit', ?)",
        (txn_id, f"UPI/SHOP {txn_id}/123456789012/UPI"),
    )
    if label is not None:
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, confidence, source) VALUES (?, ?, ?, ?, ?)",
            (f"a_{txn_id}", txn_id, label, confidence, source),
        )
    if embedded:
        conn.execute(
            "INSERT INTO embedding_meta (id, transaction_id, model_version, text_hash) VALUES (?, ?, ?, 'h')",
            (f"e_{txn_id}", txn_id, current_embedding_model()),
        )
    conn.commit()


@pytest.fixture
def matching_on():
    with patch.object(settings, "embedding_provider", "local"), patch(
        "finance_copilot.pipeline.embed.local_embeddings_installed", return_value=True
    ):
        yield


class TestCoverage:
    def test_counts_sorted_payments_and_how_many_are_ready(self, client_conn, matching_on):
        client, conn = client_conn
        _payment(conn, "ready", embedded=True)
        _payment(conn, "missing")
        # Not sorted: a guess below the review bar, no category, no answer at all.
        _payment(conn, "guess", source="llm", confidence=0.3)
        _payment(conn, "blank", label="Uncategorized")
        _payment(conn, "none", label=None)
        body = client.get("/api/embeddings/coverage").json()
        assert body == {"ready": 1, "total": 2, "unavailable": None}

    def test_a_vector_from_another_model_is_not_ready(self, client_conn, matching_on):
        client, conn = client_conn
        _payment(conn, "old", embedded=True)
        conn.execute("UPDATE embedding_meta SET model_version = 'some-other-model'")
        conn.commit()
        body = client.get("/api/embeddings/coverage").json()
        assert (body["ready"], body["total"]) == (0, 1)

    def test_says_why_when_matching_is_off(self, client_conn):
        client, _ = client_conn
        with patch.object(settings, "embedding_provider", "none"):
            body = client.get("/api/embeddings/coverage").json()
        assert "turned off" in body["unavailable"]


class TestUpdateNow:
    def test_is_refused_with_a_plain_reason_when_matching_is_off(self, client_conn):
        client, _ = client_conn
        with patch.object(settings, "embedding_provider", "none"):
            r = client.post("/api/embeddings/generate", json={})
        assert r.status_code == 409
        assert "turned off" in r.json()["detail"]

    def test_is_refused_when_the_built_in_matcher_is_missing(self, client_conn):
        client, _ = client_conn
        with patch.object(settings, "embedding_provider", "local"), patch(
            "finance_copilot.pipeline.embed.local_embeddings_installed", return_value=False
        ):
            r = client.post("/api/embeddings/generate", json={})
        assert r.status_code == 409
        assert "built-in matcher" in r.json()["detail"]

    @pytest.mark.parametrize("status", ["queued", "running"])
    def test_is_refused_while_a_job_runs(self, client_conn, status):
        client, conn = client_conn
        conn.execute("INSERT INTO annotation_jobs (id, kind, status) VALUES ('j', 'annotate', ?)", (status,))
        conn.commit()
        with patch.object(settings, "embedding_provider", "local"), patch(
            "finance_copilot.pipeline.embed.local_embeddings_installed", return_value=True
        ), patch("finance_copilot.api.routes.embeddings.refresh_embeddings") as refresh:
            r = client.post("/api/embeddings/generate", json={})
        assert r.status_code == 409
        assert r.json()["detail"] == "Finance Copilot is busy sorting. Try again when it finishes."
        refresh.assert_not_called()

    def test_an_unreachable_provider_is_a_plain_409_not_a_crash(self, client_conn):
        import httpx

        client, _ = client_conn
        with patch.object(settings, "embedding_provider", "ollama"), patch(
            "finance_copilot.api.routes.embeddings.refresh_embeddings",
            side_effect=httpx.ConnectError("refused"),
        ):
            r = client.post("/api/embeddings/generate", json={})
        assert r.status_code == 409
        assert "reach" in r.json()["detail"]

    def test_runs_when_matching_is_on(self, client_conn):
        client, _ = client_conn
        with patch.object(settings, "embedding_provider", "local"), patch(
            "finance_copilot.pipeline.embed.local_embeddings_installed", return_value=True
        ), patch(
            "finance_copilot.api.routes.embeddings.refresh_embeddings",
            return_value={"embedded": 3, "model": "m"},
        ):
            r = client.post("/api/embeddings/generate", json={})
        assert r.status_code == 200
        assert r.json() == {"embedded": 3}


def test_there_is_no_forget_route(client_conn):
    # Forget was undone by the next launch's refresh, so it was removed.
    client, _ = client_conn
    assert client.delete("/api/embeddings/statement/s").status_code in (404, 405)
