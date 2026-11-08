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


def _seed_txn(conn, txn_id: str, desc: str = "X") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) "
        "VALUES ('s1', 't', '1', '2026-01')"
    )
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
        "VALUES (?, 's1', '2026-01-10', 10.0, 'debit', ?)",
        (txn_id, desc),
    )
    conn.commit()


def _txn_ids(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT id FROM transactions")}


def _restore(client, payload: bytes):
    return client.post(
        "/api/data/restore",
        files={"file": ("finance-copilot-backup.db", payload, "application/x-sqlite3")},
    )


class TestRestore:
    def test_round_trip_brings_back_the_backed_up_ledger(self, file_db):
        client, conn, tmp = file_db
        _seed_txn(conn, "before")
        backup = client.get("/api/data/backup").content

        # Life goes on after the backup: a row is added, one is lost.
        _seed_txn(conn, "after")
        conn.execute("DELETE FROM transactions WHERE id = 'before'")
        conn.commit()

        r = _restore(client, backup)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["transactions"] == 1
        assert _txn_ids(conn) == {"before"}
        # The pre-restore ledger is kept next to the live one.
        safety = tmp / body["safety_copy"]
        assert safety.exists()
        kept = get_connection(str(safety))
        try:
            assert _txn_ids(kept) == {"after"}
        finally:
            kept.close()

    def test_keeps_the_current_api_key(self, file_db):
        # Backups never carry the key, so restoring one must not log you out of
        # your model provider.
        from finance_copilot.db.queries.app_settings import get_llm_config

        client, conn, _ = file_db
        backup = client.get("/api/data/backup").content
        client.put("/api/config/llm", json={"llm_provider": "openai", "llm_api_key": CANARY})
        assert _restore(client, backup).status_code == 200
        assert get_llm_config(conn, include_secret=True)["llm_api_key"] == CANARY

    def test_rejects_a_file_that_is_not_sqlite(self, file_db):
        client, conn, _ = file_db
        _seed_txn(conn, "keep")
        r = _restore(client, b"definitely not a database")
        assert r.status_code == 422
        assert _txn_ids(conn) == {"keep"}

    def test_rejects_a_corrupt_database(self, file_db):
        client, conn, _ = file_db
        _seed_txn(conn, "keep")
        good = client.get("/api/data/backup").content
        corrupt = good[:4096] + b"\x00" * (len(good) - 4096)
        r = _restore(client, corrupt)
        assert r.status_code == 422
        assert _txn_ids(conn) == {"keep"}

    def test_rejects_a_sqlite_file_that_is_not_a_ledger(self, file_db, tmp_path):
        import sqlite3

        client, conn, _ = file_db
        other = tmp_path / "other.db"
        c = sqlite3.connect(other)
        c.execute("CREATE TABLE notes (x TEXT)")
        c.commit()
        c.close()
        r = _restore(client, other.read_bytes())
        assert r.status_code == 422
        assert "Finance Copilot backup" in r.json()["detail"]

    def test_rejects_a_backup_from_a_newer_version(self, file_db, tmp_path):
        import sqlite3

        client, conn, _ = file_db
        backup = tmp_path / "newer.db"
        backup.write_bytes(client.get("/api/data/backup").content)
        c = sqlite3.connect(backup)
        c.execute("INSERT INTO schema_migrations (version) VALUES ('999_from_the_future.sql')")
        c.commit()
        c.close()
        r = _restore(client, backup.read_bytes())
        assert r.status_code == 422
        assert "newer version" in r.json()["detail"]

    def test_refused_while_a_job_is_running(self, file_db):
        client, conn, _ = file_db
        backup = client.get("/api/data/backup").content
        conn.execute(
            "INSERT INTO annotation_jobs (id, status, kind) VALUES ('j1', 'running', 'annotate')"
        )
        conn.commit()
        r = _restore(client, backup)
        assert r.status_code == 409

    def test_jobs_frozen_in_the_backup_do_not_block_later_runs(self, file_db, tmp_path):
        import sqlite3

        client, conn, _ = file_db
        snap = tmp_path / "snap.db"
        snap.write_bytes(client.get("/api/data/backup").content)
        c = sqlite3.connect(snap)
        c.execute("INSERT INTO annotation_jobs (id, status, kind) VALUES ('j1', 'running', 'annotate')")
        c.commit()
        c.close()
        assert _restore(client, snap.read_bytes()).status_code == 200
        status = conn.execute("SELECT status FROM annotation_jobs WHERE id = 'j1'").fetchone()[0]
        assert status == "failed"

    def test_blocked_in_demo_mode(self, file_db, monkeypatch):
        from finance_copilot.config import settings

        client, _, _ = file_db
        backup = client.get("/api/data/backup").content
        monkeypatch.setattr(settings, "demo_mode", True)
        assert _restore(client, backup).status_code == 403
