"""Run the API server: `python -m src`.

Owns the host default (127.0.0.1) so the API - and every bank statement it
serves - is loopback-only unless the user deliberately opts into LAN exposure.
There is no authentication layer yet (single-user local product), so a
non-loopback bind is a real disclosure and gets a loud warning, never a silent
copy-pasted default.

Config via environment: HOST (default 127.0.0.1), PORT (default 8000),
RELOAD (set to 1/true for auto-reload during development).
"""
from __future__ import annotations

import ipaddress
import logging
import os

import uvicorn

logger = logging.getLogger("src")

# Names ip_address() cannot parse but which are still loopback in practice.
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain"}


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A hostname we can't resolve to a literal — treat as non-loopback so we
        # err toward warning rather than staying silent.
        return False


def warn_if_exposed(host: str) -> None:
    """Warn when binding somewhere other than loopback (0.0.0.0, a LAN IP, etc.)."""
    if _is_loopback(host):
        return
    logger.warning(
        "Binding to %s exposes the API - and every bank statement it serves - to "
        "your whole network with NO authentication. Bind to 127.0.0.1 (the default) "
        "unless you specifically intend LAN access.",
        host,
    )


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "").lower() in ("1", "true", "yes")
    warn_if_exposed(host)
    uvicorn.run("src.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
