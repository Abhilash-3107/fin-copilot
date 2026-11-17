"""A second copy of the app on the same data folder stops before touching jobs."""
from __future__ import annotations

import errno
import logging
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from finance_copilot.config import settings
from finance_copilot.db import connection as dbc
from finance_copilot.db import folder_lock
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


class TestWhatCountsAsInUse:
    """Only the errors that mean "another copy holds it" stop the launch. Any
    other failure (a filesystem with no locking, say) must not lock the person
    out of their own ledger."""

    def test_an_unexpected_error_warns_and_starts_without_the_lock(self, tmp_path, monkeypatch, caplog):
        def no_locks(handle, flags):
            raise OSError(errno.ENOLCK, "No locks available")

        monkeypatch.setattr(folder_lock, "_WINDOWS", False)
        monkeypatch.setattr("fcntl.flock", no_locks)
        with caplog.at_level(logging.WARNING):
            assert lock_data_folder(tmp_path) is True
        unlock_data_folder(tmp_path)
        assert "without the lock" in caplog.text

    @pytest.mark.parametrize("code", [errno.EWOULDBLOCK, errno.EAGAIN])
    def test_a_held_lock_on_posix_means_in_use(self, tmp_path, monkeypatch, code):
        def held(handle, flags):
            raise OSError(code, "held")

        monkeypatch.setattr(folder_lock, "_WINDOWS", False)
        monkeypatch.setattr("fcntl.flock", held)
        with pytest.raises(DataFolderInUse):
            lock_data_folder(tmp_path)


class FakeMsvcrt(types.SimpleNamespace):
    def __init__(self, fail_with=None):
        super().__init__(LK_NBLCK=2, LK_UNLCK=0, calls=[], fail_with=fail_with)

    def locking(self, fd, mode, nbytes):
        self.calls.append(("locking", mode, nbytes))
        if mode == self.LK_NBLCK and self.fail_with is not None:
            raise OSError(self.fail_with, "x")


class TestWindows:
    @pytest.fixture
    def fake(self, monkeypatch):
        monkeypatch.setattr(folder_lock, "_WINDOWS", True)
        return FakeMsvcrt()

    def _install(self, monkeypatch, fake):
        monkeypatch.setitem(sys.modules, "msvcrt", fake)
        # Record seeks and closes on the lock file in the same order as the locking calls.
        real_open = open

        class Handle:
            def __init__(self, path, mode):
                self._f = real_open(path, mode)

            def fileno(self):
                return self._f.fileno()

            def seek(self, pos):
                fake.calls.append(("seek", pos))
                self._f.seek(pos)

            def close(self):
                fake.calls.append(("close",))
                self._f.close()

        monkeypatch.setattr(folder_lock, "open", Handle, raising=False)

    def test_it_locks_the_first_byte_and_unlocks_it_before_closing(self, tmp_path, monkeypatch, fake):
        self._install(monkeypatch, fake)
        assert lock_data_folder(tmp_path) is True
        unlock_data_folder(tmp_path)
        assert fake.calls == [
            ("seek", 0), ("locking", fake.LK_NBLCK, 1),
            ("seek", 0), ("locking", fake.LK_UNLCK, 1), ("close",),
        ]

    @pytest.mark.parametrize("code", [errno.EACCES, errno.EDEADLK])
    def test_a_held_lock_means_in_use(self, tmp_path, monkeypatch, fake, code):
        fake.fail_with = code
        self._install(monkeypatch, fake)
        with pytest.raises(DataFolderInUse):
            lock_data_folder(tmp_path)
        assert fake.calls[-1] == ("close",)

    def test_any_other_error_starts_without_the_lock(self, tmp_path, monkeypatch, fake, caplog):
        fake.fail_with = errno.ENOLCK
        self._install(monkeypatch, fake)
        with caplog.at_level(logging.WARNING):
            assert lock_data_folder(tmp_path) is True
        unlock_data_folder(tmp_path)
        assert "without the lock" in caplog.text


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
                patch.object(launcher, "_wait_until_healthy", return_value=False), \
                patch("uvicorn.run") as run:
            rc = launcher.main(["--data-dir", str(other_process), "--no-browser"])
        assert rc == 1
        run.assert_not_called()
        err = capsys.readouterr().err
        assert "Finance Copilot is already running. Open http://127.0.0.1:5555, or quit that copy first." in err
        # It is the other copy's file: not rewritten, not cleared.
        assert runtime.read_text() == '{"port": 5555, "pid": 1}'

    def test_a_launch_during_a_slow_startup_waits_for_the_other_copy_and_opens_it(self, other_process, capsys):
        """The other copy holds the lock but is not answering yet (a big
        ledger takes a while to open): wait for it instead of failing."""
        from finance_copilot import app as launcher

        (other_process / "runtime.json").write_text('{"port": 5555, "pid": 1}')
        answers = iter([False, False, True])  # the folder-lock check, then slow, then up
        with patch.dict("os.environ"), \
                patch.object(launcher, "_instance_alive", side_effect=lambda port: next(answers)) as alive, \
                patch.object(launcher.time, "sleep"), \
                patch.object(launcher.webbrowser, "open") as open_browser, \
                patch("uvicorn.run") as run:
            rc = launcher.main(["--data-dir", str(other_process)])
        assert rc == 0
        run.assert_not_called()
        assert alive.call_count == 3
        open_browser.assert_called_once_with("http://127.0.0.1:5555")
        assert "already running" in capsys.readouterr().out

    def test_the_wait_ends_with_no_browser_when_asked_not_to_open_one(self, other_process):
        from finance_copilot import app as launcher

        (other_process / "runtime.json").write_text('{"port": 5555, "pid": 1}')
        answers = iter([False, True])
        with patch.dict("os.environ"), \
                patch.object(launcher, "_instance_alive", side_effect=lambda port: next(answers)), \
                patch.object(launcher.time, "sleep"), \
                patch.object(launcher.webbrowser, "open") as open_browser:
            assert launcher.main(["--data-dir", str(other_process), "--no-browser"]) == 0
        open_browser.assert_not_called()

    def test_with_no_recorded_port_it_stops_at_once(self, other_process, capsys):
        from finance_copilot import app as launcher

        with patch.dict("os.environ"), \
                patch.object(launcher, "_instance_alive", return_value=False) as alive, \
                patch("uvicorn.run") as run:
            rc = launcher.main(["--data-dir", str(other_process), "--no-browser"])
        assert rc == 1
        run.assert_not_called()
        alive.assert_not_called()
        assert "already running" in capsys.readouterr().err
