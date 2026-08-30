"""The `finance-copilot` console entry point.

Boring by design: resolve the data dir, pick a free loopback port, start
uvicorn, open the browser once the app answers its health check, and print
where it is running. A runtime.json in the data dir records the live port so
a second invocation opens a browser tab to the running instance instead of
starting a second server on the same database.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from importlib import metadata
from pathlib import Path

import httpx


def _version() -> str:
    try:
        return metadata.version("finance-copilot")
    except metadata.PackageNotFoundError:
        return "0.0.0.dev0 (checkout)"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/api/health"


def _instance_alive(port: int) -> bool:
    try:
        return httpx.get(_health_url(port), timeout=1.0).status_code == 200
    except Exception:
        return False


def _read_runtime(data_dir: Path) -> dict | None:
    try:
        return json.loads((data_dir / "runtime.json").read_text())
    except Exception:
        return None


def _write_runtime(data_dir: Path, port: int) -> None:
    (data_dir / "runtime.json").write_text(
        json.dumps({"port": port, "pid": os.getpid()})
    )


def _clear_runtime(data_dir: Path) -> None:
    with contextlib.suppress(OSError):
        (data_dir / "runtime.json").unlink()


def _open_when_healthy(port: int, open_browser: bool) -> None:
    """Poll health from a daemon thread; print the URL (and open a tab) when up."""
    url = f"http://127.0.0.1:{port}"

    def poll() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if _instance_alive(port):
                print(f"\nFinance Copilot is running at {url}")
                print("Your data stays on this machine. Press Ctrl-C to quit.")
                if open_browser:
                    webbrowser.open(url)
                return
            time.sleep(0.25)
        print(f"Server did not become healthy within 30s; check {url} manually.")

    threading.Thread(target=poll, daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="finance-copilot",
        description="Local-first personal finance: parse bank statements, categorize, review.",
    )
    parser.add_argument("--version", action="version", version=f"finance-copilot {_version()}")
    parser.add_argument("--data-dir", help="where the database lives (default: platform app-data dir)")
    parser.add_argument("--port", type=int, help="fixed port (default: an OS-assigned free port)")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args(argv)

    if args.data_dir:
        # Must land in the environment before finance_copilot.config is imported;
        # everything downstream resolves paths through it.
        os.environ["FC_DATA_DIR"] = args.data_dir

    # Resolved at call time (not via the settings singleton) so --data-dir takes
    # effect regardless of whether finance_copilot.config was already imported.
    from finance_copilot.config import default_data_dir

    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    # Second-launch behavior: reuse a live instance instead of double-serving the DB.
    runtime = _read_runtime(data_dir)
    if runtime and _instance_alive(runtime.get("port", -1)):
        url = f"http://127.0.0.1:{runtime['port']}"
        print(f"Finance Copilot is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    port = args.port or _free_port()

    import uvicorn

    from finance_copilot.main import app

    _write_runtime(data_dir, port)
    _open_when_healthy(port, open_browser=not args.no_browser)
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        _clear_runtime(data_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
