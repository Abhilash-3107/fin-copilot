"""Shared UPI description parser — bank-agnostic, config-driven noise filtering."""
from __future__ import annotations

import json


def parse_upi_description(raw: str, noise_keywords: list[str]) -> str | None:
    """Return a JSON string of UPI metadata if raw starts with 'UPI/', else None.

    Structure:
        {"note": "some free text"}   # note is None if last segment is a noise keyword

    The full raw_description is already stored on the transaction, so middle segments
    are intentionally not extracted — their position and meaning vary by bank/app.
    """
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    if not parts or parts[0].upper() != "UPI":
        return None

    # Need at least UPI + one middle segment + one last segment to have a note
    if len(parts) < 3:
        return json.dumps({"note": None})

    noise_upper = {k.upper() for k in noise_keywords}
    last = parts[-1]
    note = None if last.upper() in noise_upper else last

    return json.dumps({"note": note})
