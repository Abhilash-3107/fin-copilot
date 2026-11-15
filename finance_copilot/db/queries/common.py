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


UNCATEGORIZED = "Uncategorized"


def unsorted_sql(alias: str = "a") -> str:
    """SQL predicate for "not sorted yet", over a LEFT JOINed annotations alias.

    The one definition shared by Insights (the unexplained figure and the
    Uncategorized breakdown row) and the Transactions category filter, so a
    figure and the list it links to can never count different rows: no
    annotation at all, or an annotation whose category is NULL or Uncategorized.
    A user-chosen label such as Miscellaneous is a real category, not unsorted.
    """
    return (
        f"({alias}.id IS NULL OR {alias}.category IS NULL "
        f"OR {alias}.category = '{UNCATEGORIZED}')"
    )
