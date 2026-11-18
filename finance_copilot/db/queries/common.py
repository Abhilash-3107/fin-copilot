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


def waiting_on_pair_sql(alias: str = "a", txn: str = "t") -> str:
    """SQL predicate: payment `txn` (annotations `alias`, LEFT JOINed) is held back as
    unsorted only because it could be one side of a transfer between the user's own
    accounts that pairing was not sure enough to make (db/queries/transfers.has_open_candidate_sql).

    Whatever confident machine label it carries, only the user can say it is theirs. A
    label the user made or wrote (manual, imported, user_rule) is their decision and is
    never pulled in. A payment already labelled Self Transfers (any source) is out of In
    and Out already, so pairing it moves no figure and it is not held back either.
    """
    from finance_copilot.db.queries.transfers import has_open_candidate_sql
    from finance_copilot.pipeline.sources import USER_LABEL_SOURCES

    intent = ",".join(f"'{s}'" for s in sorted(USER_LABEL_SOURCES))
    return (
        f"({alias}.source NOT IN ({intent}) AND {alias}.category IS NOT 'Self Transfers' "
        f"AND {has_open_candidate_sql(txn)})"
    )


def unsorted_sql(alias: str = "a", txn: str = "t") -> str:
    """SQL predicate for "not sorted yet", over a payment `txn` LEFT JOINed to its
    annotations `alias`.

    The one definition shared by Insights (every figure that groups by category, via
    effective_category_sql), the review queue, the sorted counts and coverage, and the
    Transactions category filter, so a figure and the list it links to can never count
    different rows. A payment is not sorted when
      - it has no annotation, or its category is NULL or Uncategorized, or
      - it is waiting on a pair (waiting_on_pair_sql).
    A user-chosen label such as Miscellaneous is a real category, not unsorted.
    """
    return (
        f"({alias}.id IS NULL OR {alias}.category IS NULL "
        f"OR {alias}.category = '{UNCATEGORIZED}' "
        f"OR {waiting_on_pair_sql(alias, txn)})"
    )


def effective_category_sql(alias: str = "a", txn: str = "t") -> str:
    """SQL expression: the category a payment counts under in every money figure.

    Uncategorized when it is not sorted (unsorted_sql), else its category, so the
    Dashboard's "isn't sorted yet" figure, the Money Map Uncategorized row and the
    Transactions Uncategorized filter are the same rows, and In, Out, Invested and
    every breakdown add up with it. An open own-account candidate whose machine label
    was Investments therefore counts as Uncategorized Out until the user answers it.
    """
    return (
        f"(CASE WHEN {waiting_on_pair_sql(alias, txn)} THEN '{UNCATEGORIZED}' "
        f"ELSE COALESCE({alias}.category, '{UNCATEGORIZED}') END)"
    )


def effective_subcategory_sql(alias: str = "a", txn: str = "t") -> str:
    """SQL expression: the subcategory that goes with effective_category_sql (none when
    the payment counts as Uncategorized only because it is waiting on a pair)."""
    return f"(CASE WHEN {waiting_on_pair_sql(alias, txn)} THEN NULL ELSE {alias}.subcategory END)"
