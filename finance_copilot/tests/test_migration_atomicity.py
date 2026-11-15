"""A migration file applies fully or not at all (and is retried cleanly)."""
from __future__ import annotations

import sqlite3

import pytest

from finance_copilot.db import connection
from finance_copilot.db.connection import get_connection, init_db


def _columns(conn, table):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_a_migration_failing_on_its_last_statement_leaves_no_partial_change(tmp_path, monkeypatch):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    monkeypatch.setattr(connection, "MIGRATIONS_DIR", migrations)
    (migrations / "001_base.sql").write_text("CREATE TABLE thing (id TEXT PRIMARY KEY);\n")
    broken = (
        "ALTER TABLE thing ADD COLUMN extra TEXT;\n"
        "INSERT INTO thing (id, extra) VALUES ('a', 'b');\n"
        "INSERT INTO missing_table VALUES (1);\n"
    )
    (migrations / "002_broken.sql").write_text(broken)

    conn = get_connection(str(tmp_path / "ledger.db"))
    with pytest.raises(sqlite3.OperationalError):
        init_db(conn)

    # The ALTER and the INSERT rolled back with the failing statement.
    assert _columns(conn, "thing") == ["id"]
    assert conn.execute("SELECT COUNT(*) FROM thing").fetchone()[0] == 0
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    assert applied == {"001_base.sql"}

    # Fixing the file and starting again applies it from the top, once.
    (migrations / "002_broken.sql").write_text(broken.replace("INSERT INTO missing_table VALUES (1);\n", ""))
    init_db(get_connection(str(tmp_path / "ledger.db")))
    conn = get_connection(str(tmp_path / "ledger.db"))
    assert _columns(conn, "thing") == ["id", "extra"]
    assert conn.execute("SELECT COUNT(*) FROM thing").fetchone()[0] == 1
    assert {r[0] for r in conn.execute("SELECT version FROM schema_migrations")} == {"001_base.sql", "002_broken.sql"}
