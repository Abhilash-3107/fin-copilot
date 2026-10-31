"""Data-management routes: backup download (and, below, restore)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from finance_copilot.db.connection import get_connection, init_db


@pytest.fixture
def file_db(tmp_path, monkeypatch):
    """A real on-disk ledger: backup/restore work on settings.db_path, not a
    request connection, so an in-memory DB can't stand in."""
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.config import settings
    from finance_copilot.main import app

    db_path = tmp_path / "finance.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    conn = get_connection(str(db_path))
    init_db(conn)
    app.dependency_overrides[api_get_db] = lambda: conn
    yield TestClient(app), conn, tmp_path
    app.dependency_overrides.pop(api_get_db, None)
    conn.close()


CANARY = "sk-BACKUP-CANARY-9f2e"


class TestBackup:
    def test_backup_never_contains_the_api_key(self, file_db):
        # Review M4: a shared backup (bug report, new laptop) shipped the BYOM key.
        client, conn, tmp = file_db
        r = client.put("/api/config/llm", json={"llm_provider": "openai", "llm_api_key": CANARY})
        assert r.status_code == 200

        resp = client.get("/api/data/backup")
        assert resp.status_code == 200
        # Not in the table, and not lingering on a free page either.
        assert CANARY.encode() not in resp.content
        out = tmp / "downloaded.db"
        out.write_bytes(resp.content)
        copy = get_connection(str(out))
        try:
            keys = {r[0] for r in copy.execute("SELECT key FROM app_settings")}
            assert "llm_api_key" not in keys
            assert "llm_provider" in keys  # the rest of the settings survive
        finally:
            copy.close()

    def test_backup_leaves_the_live_key_alone(self, file_db):
        from finance_copilot.db.queries.app_settings import get_llm_config

        client, conn, _ = file_db
        client.put("/api/config/llm", json={"llm_provider": "openai", "llm_api_key": CANARY})
        client.get("/api/data/backup")
        assert get_llm_config(conn, include_secret=True)["llm_api_key"] == CANARY
