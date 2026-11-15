"""The CLI only reports the version; the review queue lives in the web app."""
from __future__ import annotations

from typer.testing import CliRunner

from finance_copilot import app_version
from finance_copilot.cli import app

runner = CliRunner()


def test_version_command():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert app_version() in result.output


def test_there_is_no_terminal_review_command():
    assert runner.invoke(app, ["review"]).exit_code != 0
