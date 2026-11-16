"""A second copy of the app on the same data folder stops before touching jobs."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from finance_copilot.config import settings
from finance_copilot.db import connection as dbc
from finance_copilot.db.folder_lock import DataFolderInUse, lock_data_folder, unlock_data_folder

HOLDER = (
    "import sys\n"
    "from finance_copilot.db.folder_lock import lock_data_folder\n"
    "lock_data_folder(sys.argv[1])\n"
    "print('held', flush=True)\n"
    "sys.stdin.readline()\n"
)


@pytest.fixture
def other_process(tmp_path):
    """A separate process that holds the lock on the test's data folder."""
    folder = Path(settings.data_dir)
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", HOLDER, str(folder)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "held"
    yield folder
    child.stdin.close()
    child.wait(10)


def test_a_second_process_cannot_take_the_folder(other_process):
    with pytest.raises(DataFolderInUse, match="already running"):
        lock_data_folder(other_process)


def test_the_lock_is_free_again_once_the_holder_exits(tmp_path):
    folder = tmp_path / "data"
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", HOLDER, str(folder)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "held"
    with pytest.raises(DataFolderInUse):
        lock_data_folder(folder)
    child.stdin.close()
    child.wait(10)
    assert lock_data_folder(folder) is True
    unlock_data_folder(folder)


def test_asking_twice_in_one_process_is_fine_and_only_the_first_took_it(tmp_path):
    assert lock_data_folder(tmp_path) is True
    try:
        assert lock_data_folder(tmp_path) is False
    finally:
        unlock_data_folder(tmp_path)
    assert lock_data_folder(tmp_path) is True
    unlock_data_folder(tmp_path)


def test_startup_on_a_folder_another_copy_owns_leaves_its_jobs_alone(other_process):
    from finance_copilot.main import app

    conn = dbc.get_migrated_db(settings.db_path)
    conn.execute("INSERT INTO annotation_jobs (id, status, kind) VALUES ('theirs', 'running', 'annotate')")
    conn.commit()
    try:
        with pytest.raises(DataFolderInUse), TestClient(app):
            pass
        row = conn.execute("SELECT status, error FROM annotation_jobs WHERE id = 'theirs'").fetchone()
        assert (row["status"], row["error"]) == ("running", None)
    finally:
        conn.close()


def test_startup_releases_the_folder_when_the_app_stops():
    from finance_copilot.main import app

    with TestClient(app):
        pass
    assert lock_data_folder(settings.data_dir) is True
    unlock_data_folder(settings.data_dir)


class TestLauncher:
    def test_a_launch_beside_a_running_copy_says_so_and_leaves_its_runtime_file(self, other_process, capsys):
        from finance_copilot import app as launcher

        runtime = other_process / "runtime.json"
        runtime.write_text('{"port": 5555, "pid": 1}')
        with patch.dict("os.environ"), \
                patch.object(launcher, "_instance_alive", return_value=False), \
                patch("uvicorn.run") as run:
            rc = launcher.main(["--data-dir", str(other_process), "--no-browser"])
        assert rc == 1
        run.assert_not_called()
        assert "already running" in capsys.readouterr().err
        # It is the other copy's file: not rewritten, not cleared.
        assert runtime.read_text() == '{"port": 5555, "pid": 1}'
