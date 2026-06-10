"""Small helpers shared across query modules."""
from __future__ import annotations

import json


def parse_string_list(value) -> list[str]:
    """Parse a JSON-array TEXT column into a list of strings.

    Tolerates legacy comma-separated values (pre-migration-014 rows or seeds)
    and None/empty.
    """
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return [part for part in str(value).split(",") if part]


def dump_string_list(values: list[str] | None) -> str:
    """Serialize a list of strings for a JSON-array TEXT column."""
    return json.dumps(values or [])
