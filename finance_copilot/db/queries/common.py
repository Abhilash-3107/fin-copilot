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


def unsorted_sql(alias: str = "a", txn: str = "t") -> str:
    """SQL predicate for "needs you", over a payment `txn` LEFT JOINed to its
    annotations `alias`.

    The one definition shared by Insights (the unexplained figure), the review
    queue, the sorted counts and coverage, and the Transactions category filter,
    so a figure and the list it links to can never count different rows. A payment
    needs the user when
      - it has no annotation, or its category is NULL or Uncategorized, or
      - it could be one side of a transfer between their own accounts that pairing
        was not sure enough to make (db/queries/transfers.has_open_candidate_sql),
        whatever confident machine label it carries: only the user can say it is
        theirs. A label the user made or wrote (manual, imported, user_rule) is
        their decision, so it is never pulled in by this.
    A user-chosen label such as Miscellaneous is a real category, not unsorted.
    """
    from finance_copilot.db.queries.transfers import has_open_candidate_sql
    from finance_copilot.pipeline.sources import USER_LABEL_SOURCES

    intent = ",".join(f"'{s}'" for s in sorted(USER_LABEL_SOURCES))
    return (
        f"({alias}.id IS NULL OR {alias}.category IS NULL "
        f"OR {alias}.category = '{UNCATEGORIZED}' "
        f"OR ({alias}.source NOT IN ({intent}) AND {has_open_candidate_sql(txn)}))"
    )
