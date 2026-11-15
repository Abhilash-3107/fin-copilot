"""The app's notion of "today".

Anything that anchors generated data to the current date goes through today(),
so the end-to-end suite can pin it with FC_TODAY=YYYY-MM-DD and see the same
ledger on every run. Timestamps for files and logs are not affected.
"""
from __future__ import annotations

import os
from datetime import date

ENV_VAR = "FC_TODAY"


def today() -> date:
    """FC_TODAY when set, else the real date. A malformed FC_TODAY raises."""
    pinned = os.environ.get(ENV_VAR, "").strip()
    if not pinned:
        return date.today()
    try:
        return date.fromisoformat(pinned)
    except ValueError:
        raise ValueError(f"{ENV_VAR} must be a date like 2026-11-15, got {pinned!r}") from None
