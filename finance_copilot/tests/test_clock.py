"""finance_copilot.clock: the one place that decides what "today" is."""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finance_copilot import clock
from finance_copilot.db.connection import init_db


def test_returns_real_date_when_unset(monkeypatch):
    monkeypatch.delenv("FC_TODAY", raising=False)
    assert clock.today() == date.today()


def test_blank_value_counts_as_unset(monkeypatch):
    monkeypatch.setenv("FC_TODAY", "  ")
    assert clock.today() == date.today()


def test_env_pins_the_date(monkeypatch):
    monkeypatch.setenv("FC_TODAY", "2026-11-15")
    assert clock.today() == date(2026, 11, 15)


@pytest.mark.parametrize("bad", ["tomorrow", "2026-13-01", "15/11/2026"])
def test_invalid_value_raises_a_clear_error(monkeypatch, bad):
    monkeypatch.setenv("FC_TODAY", bad)
    with pytest.raises(ValueError, match="FC_TODAY must be a date like 2026-11-15"):
        clock.today()


def test_app_refuses_to_start_with_invalid_value(monkeypatch):
    from finance_copilot.main import app

    monkeypatch.setenv("FC_TODAY", "nope")
    with pytest.raises(ValueError, match="FC_TODAY"), TestClient(app):
        pass


def test_demo_route_anchors_to_the_pinned_date(monkeypatch):
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.main import app

    monkeypatch.setenv("FC_TODAY", "2026-11-15")
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    app.dependency_overrides[api_get_db] = lambda: conn
    try:
        assert TestClient(app).post("/api/data/demo").status_code == 200
        newest = conn.execute("SELECT MAX(txn_date) FROM transactions").fetchone()[0]
        months = {r[0] for r in conn.execute("SELECT DISTINCT statement_month FROM statements")}
    finally:
        app.dependency_overrides.pop(api_get_db, None)
        conn.close()
    assert newest == "2026-11-15"
    assert months == {"2026-08", "2026-09", "2026-10", "2026-11"}


def test_seed_cli_today_flag(tmp_path, monkeypatch):
    from finance_copilot import demo_seed

    db = tmp_path / "demo.db"
    monkeypatch.setattr("sys.argv", ["seed", "--db", str(db), "--today", "2026-02-10"])
    demo_seed.main()
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT MAX(txn_date) FROM transactions").fetchone()[0] == "2026-02-10"
    finally:
        conn.close()
