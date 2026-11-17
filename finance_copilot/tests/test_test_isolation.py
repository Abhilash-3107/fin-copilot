"""No test can reach the default ledger (<repo>/data/finance.db)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from finance_copilot.config import settings
from finance_copilot.db import connection as dbc

REPO_LEDGER = Path(__file__).resolve().parents[2] / "data" / "finance.db"
TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


def test_settings_point_at_a_temp_folder_for_every_test():
    for value in (settings.db_path, settings.data_dir, os.environ["FC_DATA_DIR"]):
        assert TEMP_ROOT in Path(value).resolve().parents
    assert Path(settings.db_path).resolve() != REPO_LEDGER


def test_the_default_ledger_cannot_be_opened_from_a_test():
    with pytest.raises(AssertionError, match="outside"):
        dbc.get_connection(str(REPO_LEDGER))


def test_the_job_runner_connection_is_a_temp_database():
    from finance_copilot.api.routes.annotations import _open_job_connection

    conn = _open_job_connection()
    try:
        path = conn.execute("PRAGMA database_list").fetchone()["file"]
    finally:
        conn.close()
    assert Path(path).resolve() != REPO_LEDGER
    assert TEMP_ROOT in Path(path).resolve().parents
