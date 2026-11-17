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

    def test_a_sort_frozen_in_the_backup_goes_back_in_line_and_other_jobs_fail(self, file_db, tmp_path):
        import sqlite3

        client, conn, _ = file_db
        snap = tmp_path / "snap.db"
        snap.write_bytes(client.get("/api/data/backup").content)
        c = sqlite3.connect(snap)
        c.execute("INSERT INTO annotation_jobs (id, status, kind) VALUES ('j1', 'running', 'annotate')")
        c.execute("INSERT INTO annotation_jobs (id, status, kind) VALUES ('j2', 'running', 'reembed')")
        c.commit()
        c.close()
        assert _restore(client, snap.read_bytes()).status_code == 200
        statuses = dict(conn.execute("SELECT id, status FROM annotation_jobs").fetchall())
        # The sort was cut off by the snapshot, not broken: it runs against the restored ledger.
        assert statuses == {"j1": "queued", "j2": "failed"}

    def test_blocked_in_demo_mode(self, file_db, monkeypatch):
        from finance_copilot.config import settings

        client, _, _ = file_db
        backup = client.get("/api/data/backup").content
        monkeypatch.setattr(settings, "demo_mode", True)
        assert _restore(client, backup).status_code == 403


class TestAccountsInBackupsAndWipes:
    def test_restoring_a_ledger_made_before_accounts_gives_every_statement_one(self, file_db, tmp_path, monkeypatch):
        """A 0.2.0 backup has no accounts table. Restore swaps the file in and
        migrates it (reconcile_database), so migration 041 assigns the accounts."""
        import shutil
        import sqlite3

        from finance_copilot.db import connection

        client, conn, _ = file_db
        old_migrations = tmp_path / "migrations_040"
        old_migrations.mkdir()
        for path in sorted(connection.MIGRATIONS_DIR.glob("*.sql")):
            if path.name < "041":
                shutil.copy(path, old_migrations / path.name)
        pre_r3 = tmp_path / "pre-r3.db"
        with monkeypatch.context() as patched:
            patched.setattr(connection, "MIGRATIONS_DIR", old_migrations)
            old = get_connection(str(pre_r3))
            init_db(old)
            old.executemany(
                "INSERT INTO statements (id, bank_name, parser_version, statement_month, account_ref) "
                "VALUES (?, ?, '1', ?, ?)",
                [("s1", "kotak", "2026-01", "3250508074"), ("s2", "kotak", "2026-02", None),
                 ("s3", "hdfc (auto)", "2026-02", "XXXX5678")],
            )
            old.commit()
            old.close()
        probe = sqlite3.connect(pre_r3)
        assert "accounts" not in {r[0] for r in probe.execute("SELECT name FROM sqlite_master")}
        probe.close()

        r = _restore(client, pre_r3.read_bytes())
        assert r.status_code == 200, r.text
        live = get_connection(str(tmp_path / "finance.db"))
        try:
            assert live.execute("SELECT COUNT(*) FROM statements WHERE account_id IS NULL").fetchone()[0] == 0
            assert live.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2
        finally:
            live.close()
        labels = {a["account_label"] for a in client.get("/api/accounts").json()}
        assert labels == {"Kotak ··8074", "HDFC ··5678"}

    def test_a_backup_round_trip_keeps_the_accounts_and_their_names(self, file_db):
        client, conn, _ = file_db
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref, name) VALUES ('a1', 'kotak', '1234', 'Salary')")
        conn.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month, account_id) "
            "VALUES ('s1', 'kotak', '1', '2026-01', 'a1')"
        )
        conn.commit()
        backup = client.get("/api/data/backup").content
        conn.execute("UPDATE accounts SET name = 'Changed'")
        conn.commit()
        assert _restore(client, backup).status_code == 200
        assert [a["account_label"] for a in client.get("/api/accounts").json()] == ["Salary"]

    def test_delete_everything_removes_the_accounts_too(self, file_db):
        client, conn, _ = file_db
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES ('a1', 'kotak', '1234')")
        conn.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month, account_id) "
            "VALUES ('s1', 'kotak', '1', '2026-01', 'a1')"
        )
        conn.commit()
        assert client.post("/api/data/delete-all").status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 0
