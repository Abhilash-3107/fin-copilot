"""Typer CLI. The review queue lives in the web app (Teach Me), not here."""
from __future__ import annotations

import typer
from rich.console import Console

from finance_copilot import app_version

app = typer.Typer(help="Finance Copilot CLI", no_args_is_help=True)
console = Console()


@app.callback()
def main():
    """Finance Copilot CLI."""


@app.command()
def version():
    """Show version."""
    console.print(f"finance-copilot {app_version()}")


if __name__ == "__main__":
    app()
