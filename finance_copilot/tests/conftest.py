"""Shared test isolation."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

_real_connect = sqlite3.connect
_TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


def _only_temp_databases(database, *args, **kwargs):
    """Refuse any on-disk database outside the system temp folder.

    The default ledger (<repo>/data/finance.db) is the owner's real data: a
    background runner, a re-embed or a backup that opens it from a test would
    sort, reap or overwrite it. Every test path is under tmp, so anything else
    is a bug in the test, and it fails loudly instead of touching real data."""
    if isinstance(database, bytes):
        database = os.fsdecode(database)
    target = str(database)
    # "" is SQLite's anonymous temporary database: a private file it deletes on close.
    if target != "" and target != ":memory:" and not target.startswith("file::memory:"):
        raw = target[len("file:"):].split("?")[0] if target.startswith("file:") else target
        path = Path(raw).expanduser().resolve()
        if _TEMP_ROOT not in path.parents:
            raise AssertionError(
                f"a test tried to open {path}, which is outside {_TEMP_ROOT}. "
                "Tests must use tmp_path; the real ledger is never opened from a test."
            )
    return _real_connect(database, *args, **kwargs)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path_factory, monkeypatch):
    """Point every path the app resolves from settings at a temp folder, and
    make opening anything outside the temp folder an error.

    The inline job runner (below) and background re-embeds open their own
    connection from settings.db_path, which in a dev checkout is the real
    <repo>/data/finance.db. Without this a route test would reap and run real
    sorting jobs on the owner's ledger with their configured model."""
    from finance_copilot.config import settings

    data_dir = tmp_path_factory.mktemp("data")
    monkeypatch.setenv("FC_DATA_DIR", str(data_dir))
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "db_path", str(data_dir / "finance.db"))
    monkeypatch.setattr(sqlite3, "connect", _only_temp_databases)
    assert "FC_DATA_DIR" in os.environ


@pytest.fixture(autouse=True)
def _isolate_llm_settings(monkeypatch):
    """PUT /api/config/llm applies the new model config onto the process-wide
    settings singleton. Snapshot those fields so a test that saves a config
    can't change which provider every later test runs against."""
    from finance_copilot.config import settings
    from finance_copilot.db.queries.app_settings import _LLM_KEYS

    for key in _LLM_KEYS:
        monkeypatch.setattr(settings, key, getattr(settings, key))


@pytest.fixture(autouse=True)
def _run_queued_jobs_inline(monkeypatch):
    """Queued sorting jobs run on the job runner's thread, which only the app's
    lifespan starts. Without it a route test would never see its job run, so
    waking the runner runs the oldest queued job right here instead: the same
    code path, deterministic and on the test's own thread."""
    from finance_copilot.api.routes.annotations import runner

    monkeypatch.setattr(runner, "wake", runner.run_one)
