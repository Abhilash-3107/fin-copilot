"""No test can reach the default ledger (<repo>/data/finance.db)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from finance_copilot.config import default_data_dir, settings
from finance_copilot.db import connection as dbc


def _default_ledger() -> Path:
    """Where the app keeps its ledger with no override: <repo>/data in a checkout,
    the platform app-data folder for the installed wheel. FC_DATA_DIR is ignored
    here, since the suite points it at a temp folder."""
    with mock.patch.dict(os.environ):
        os.environ.pop("FC_DATA_DIR", None)
        return default_data_dir().resolve() / "finance.db"


REPO_LEDGER = _default_ledger()
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
