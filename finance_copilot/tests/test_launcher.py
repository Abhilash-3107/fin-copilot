"""Launcher behavior: data-dir resolution, runtime.json, second-launch reuse."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from finance_copilot import app as launcher
from finance_copilot.config import default_data_dir


class TestDefaultDataDir:
    def test_fc_data_dir_env_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FC_DATA_DIR", str(tmp_path / "custom"))
        assert default_data_dir() == tmp_path / "custom"

    # Both branches are simulated by pointing config's __file__ at a fake tree,
    # so the tests hold whether they run from a checkout or (in the release
    # workflow) against the installed wheel.
    def test_dev_checkout_uses_repo_data_dir(self, monkeypatch, tmp_path):
        from finance_copilot import config

        monkeypatch.delenv("FC_DATA_DIR", raising=False)
        (tmp_path / "pyproject.toml").write_text("")
        monkeypatch.setattr(config, "__file__", str(tmp_path / "finance_copilot" / "config.py"))
        assert default_data_dir() == tmp_path / "data"

    def test_installed_copy_uses_platform_dir(self, monkeypatch, tmp_path):
        import platformdirs

        from finance_copilot import config

        monkeypatch.delenv("FC_DATA_DIR", raising=False)
        monkeypatch.setattr(config, "__file__", str(tmp_path / "site-packages" / "finance_copilot" / "config.py"))
        assert default_data_dir() == Path(platformdirs.user_data_dir("FinanceCopilot", appauthor=False))

    def test_fc_data_dir_beats_db_path_from_dotenv(self, monkeypatch, tmp_path):
        # A .env with DB_PATH (the README's old dev example) must not silently
        # redirect `--data-dir X` to a different database.
        from finance_copilot.config import Settings

        env_file = tmp_path / ".env"
        env_file.write_text("DB_PATH=elsewhere/finance.db\n")
        monkeypatch.setenv("FC_DATA_DIR", str(tmp_path / "custom"))
        s = Settings(_env_file=env_file)
        assert Path(s.data_dir) == tmp_path / "custom"
        assert Path(s.db_path) == tmp_path / "custom" / "finance.db"

    def test_db_path_from_dotenv_still_works_without_fc_data_dir(self, monkeypatch, tmp_path):
        from finance_copilot.config import Settings

        env_file = tmp_path / ".env"
        env_file.write_text("DB_PATH=elsewhere/finance.db\n")
        monkeypatch.delenv("FC_DATA_DIR", raising=False)
        s = Settings(_env_file=env_file)
        assert s.db_path == "elsewhere/finance.db"


class TestRuntimeFile:
    def test_roundtrip(self, tmp_path):
        launcher._write_runtime(tmp_path, 4321)
        data = launcher._read_runtime(tmp_path)
        assert data["port"] == 4321
        assert data["pid"] > 0
        launcher._clear_runtime(tmp_path)
        assert launcher._read_runtime(tmp_path) is None

    def test_read_garbage_returns_none(self, tmp_path):
        (tmp_path / "runtime.json").write_text("not json")
        assert launcher._read_runtime(tmp_path) is None


class TestSecondLaunch:
    def test_reuses_live_instance(self, tmp_path, capsys):
        (tmp_path / "runtime.json").write_text(json.dumps({"port": 5555, "pid": 1}))
        with (
            patch.dict(os.environ),
            patch.object(launcher, "_instance_alive", return_value=True) as alive,
            patch.object(launcher.webbrowser, "open") as open_browser,
        ):
            rc = launcher.main(["--data-dir", str(tmp_path)])
        assert rc == 0
        alive.assert_called_once_with(5555)
        open_browser.assert_called_once_with("http://127.0.0.1:5555")
        assert "already running" in capsys.readouterr().out

    def test_no_browser_flag_suppresses_tab(self, tmp_path):
        (tmp_path / "runtime.json").write_text(json.dumps({"port": 5555, "pid": 1}))
        with (
            patch.dict(os.environ),
            patch.object(launcher, "_instance_alive", return_value=True),
            patch.object(launcher.webbrowser, "open") as open_browser,
        ):
            rc = launcher.main(["--data-dir", str(tmp_path), "--no-browser"])
        assert rc == 0
        open_browser.assert_not_called()

    def test_stale_runtime_is_ignored(self, tmp_path):
        """Dead recorded instance: launcher proceeds to start a server."""
        (tmp_path / "runtime.json").write_text(json.dumps({"port": 5555, "pid": 1}))
        with (
            patch.dict(os.environ),
            patch.object(launcher, "_instance_alive", return_value=False),
            patch.object(launcher, "_free_port", return_value=6789),
            patch.object(launcher, "_open_when_healthy"),
            patch("uvicorn.run") as run,
        ):
            rc = launcher.main(["--data-dir", str(tmp_path), "--no-browser"])
        assert rc == 0
        assert run.call_args.kwargs["port"] == 6789
        assert run.call_args.kwargs["host"] == "127.0.0.1"
        # runtime.json cleared after uvicorn returns
        assert launcher._read_runtime(tmp_path) is None
