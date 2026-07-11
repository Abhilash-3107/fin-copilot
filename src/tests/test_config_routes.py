"""Runtime config: threshold override, BYOM/LLM config, and export (roadmap 3.9, 3.11)."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from src.config import settings
from src.db.connection import init_db
from src.db.queries.app_settings import get_confidence_threshold, get_llm_config


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


@pytest.fixture
def client_conn():
    from src.api.deps import get_db as api_get_db
    from src.main import app

    conn = _make_conn()
    app.dependency_overrides[api_get_db] = lambda: conn
    client = TestClient(app)
    yield client, conn
    app.dependency_overrides.pop(api_get_db, None)
    conn.close()


class TestThreshold:
    def test_default_is_config_value(self, client_conn):
        client, _ = client_conn
        assert client.get("/api/config").json()["confidence_threshold"] == settings.confidence_threshold

    def test_put_persists_and_reads_back(self, client_conn):
        client, conn = client_conn
        r = client.put("/api/config", json={"confidence_threshold": 0.7})
        assert r.json()["confidence_threshold"] == 0.7
        assert get_confidence_threshold(conn) == 0.7

    def test_out_of_range_rejected(self, client_conn):
        client, _ = client_conn
        assert client.put("/api/config", json={"confidence_threshold": 1.5}).status_code == 422

    def test_dev_mode_unaffected_by_threshold_put(self, client_conn):
        client, _ = client_conn
        client.put("/api/config", json={"dev_mode": True})
        client.put("/api/config", json={"confidence_threshold": 0.6})
        assert client.get("/api/config").json()["dev_mode"] is True


class TestLlmConfig:
    def test_get_redacts_api_key(self, client_conn):
        client, _ = client_conn
        body = client.get("/api/config/llm").json()
        assert "llm_api_key" not in body
        assert body["llm_api_key_set"] is False
        assert body["llm_provider"] == settings.llm_provider

    def test_put_persists_and_applies_to_settings(self, client_conn):
        client, conn = client_conn
        client.put("/api/config/llm", json={"llm_provider": "openai", "llm_model": "gpt-4o-mini", "llm_base_url": "https://x/v1"})
        stored = get_llm_config(conn, include_secret=True)
        assert stored["llm_provider"] == "openai"
        assert stored["llm_model"] == "gpt-4o-mini"
        # Applied onto the live singleton so the pipeline reads it.
        assert settings.llm_provider == "openai"

    def test_blank_key_keeps_existing(self, client_conn):
        client, conn = client_conn
        client.put("/api/config/llm", json={"llm_provider": "openai", "llm_api_key": "secret-1"})
        client.put("/api/config/llm", json={"llm_provider": "openai", "llm_api_key": ""})
        assert get_llm_config(conn, include_secret=True)["llm_api_key"] == "secret-1"
        assert client.get("/api/config/llm").json()["llm_api_key_set"] is True

    def test_invalid_provider_rejected(self, client_conn):
        client, _ = client_conn
        assert client.put("/api/config/llm", json={"llm_provider": "gemini"}).status_code == 422

    def teardown_method(self):
        # Reset the mutated singleton so provider changes don't leak across tests.
        settings.llm_provider = "ollama"


class TestExport:
    def _seed(self, conn):
        conn.execute("INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','t','1','2026-01')")
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) VALUES ('t1','s1','2026-01-05',100,'debit','COFFEE')"
        )
        conn.commit()

    def test_csv_export(self, client_conn):
        client, conn = client_conn
        self._seed(conn)
        r = client.get("/api/transactions/export?format=csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "COFFEE" in r.text
        assert r.text.splitlines()[0].startswith("id,txn_date,amount")

    def test_json_export(self, client_conn):
        client, conn = client_conn
        self._seed(conn)
        r = client.get("/api/transactions/export?format=json")
        assert r.status_code == 200
        rows = r.json()
        assert rows[0]["raw_description"] == "COFFEE"

    def test_bad_format_rejected(self, client_conn):
        client, _ = client_conn
        assert client.get("/api/transactions/export?format=xml").status_code == 422
