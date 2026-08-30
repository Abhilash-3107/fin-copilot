"""POST /api/data/demo: seed the bundled demo ledger, never into a real one."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from finance_copilot.db.connection import init_db


@pytest.fixture
def client_conn():
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.main import app

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    app.dependency_overrides[api_get_db] = lambda: conn
    yield TestClient(app), conn
    app.dependency_overrides.pop(api_get_db, None)
    conn.close()


class TestLoadDemoData:
    def test_seeds_fresh_db(self, client_conn):
        client, conn = client_conn
        r = client.post("/api/data/demo")
        assert r.status_code == 200
        body = r.json()
        assert body["transactions"] > 100
        assert body["months"] == 4
        assert body["review_backlog"] > 0
        txns = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert txns == body["transactions"]

    def test_second_call_is_idempotent(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        first = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        r = client.post("/api/data/demo")
        assert r.status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == first

    def test_refused_when_real_statements_exist(self, client_conn):
        client, conn = client_conn
        conn.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month) "
            "VALUES ('real1', 'kotak', '1', '2026-05')"
        )
        r = client.post("/api/data/demo")
        assert r.status_code == 409
        assert "real statements" in r.json()["detail"]
