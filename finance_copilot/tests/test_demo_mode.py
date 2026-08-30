"""FC_DEMO_MODE guardrails: writes that could carry real data are blocked at the API."""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from finance_copilot.config import settings
from finance_copilot.db.connection import init_db


@pytest.fixture
def demo_client():
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.main import app

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    app.dependency_overrides[api_get_db] = lambda: conn
    with patch.object(settings, "demo_mode", True):
        yield TestClient(app), conn
    app.dependency_overrides.pop(api_get_db, None)
    conn.close()


class TestDemoModeBlocks:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/api/statements/upload"),
            ("DELETE", "/api/statements/some-id"),
            ("GET", "/api/data/backup"),
            ("POST", "/api/data/delete-all"),
            ("PUT", "/api/config/llm"),
            ("POST", "/api/config/llm/test"),
        ],
    )
    def test_blocked_with_friendly_403(self, demo_client, method, path):
        client, _ = demo_client
        r = client.request(method, path)
        assert r.status_code == 403
        assert "demo" in r.json()["detail"].lower()

    def test_interactive_paths_still_work(self, demo_client):
        client, _ = demo_client
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/transactions").status_code == 200
        assert client.get("/api/annotations/review-queue").status_code == 200
        # The review-bar slider persists via PUT /api/config - allowed.
        r = client.put("/api/config", json={"confidence_threshold": 0.9})
        assert r.status_code == 200

    def test_health_reports_demo_mode(self, demo_client):
        client, _ = demo_client
        assert client.get("/api/health").json()["demo_mode"] is True


def test_demo_mode_off_by_default():
    assert settings.demo_mode is False
