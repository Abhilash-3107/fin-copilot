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
# The top-level category of payments between people (Peer Transfer and similar).
TRANSFERS_CATEGORY = "Transfers"


def waiting_on_pair_sql(alias: str = "a", txn: str = "t") -> str:
    """SQL predicate: payment `txn` (annotations `alias`, LEFT JOINed) carries a guess in
    the Transfers category and could be one side of a transfer between the user's own
    accounts that pairing was not sure enough to make (db/queries/transfers.has_open_candidate_sql),
    so the guess is held back and the payment waits for the user.

    Only that guess. A payment with no label or Uncategorized is unsorted already, and any
    other label (Food & Dining, Investments, ...) is kept even when a payment of the same
    amount sits in another account within three days: ordinary payments are not asked
    about because of a coincidence. A label the user made or wrote (manual, imported,
    user_rule) is their decision and never held back. A payment already labelled Self
    Transfers (any source) is out of In and Out already, so it is not held back either.
    The cheap label test comes first: only Transfers-labelled payments ever pay for the
    candidate lookup.
    """
    from finance_copilot.db.queries.transfers import has_open_candidate_sql
    from finance_copilot.pipeline.sources import USER_LABEL_SOURCES

    intent = ",".join(f"'{s}'" for s in sorted(USER_LABEL_SOURCES))
    return (
        f"({alias}.category = '{TRANSFERS_CATEGORY}' AND {alias}.source NOT IN ({intent}) "
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
    every breakdown add up with it. A Transfers guess with an open own-account candidate
    therefore counts as Uncategorized until the user answers it.
    """
    return (
        f"(CASE WHEN {waiting_on_pair_sql(alias, txn)} THEN '{UNCATEGORIZED}' "
        f"ELSE COALESCE({alias}.category, '{UNCATEGORIZED}') END)"
    )


def effective_subcategory_sql(alias: str = "a", txn: str = "t") -> str:
    """SQL expression: the subcategory that goes with effective_category_sql (none when
    the payment counts as Uncategorized only because it is waiting on a pair)."""
    return f"(CASE WHEN {waiting_on_pair_sql(alias, txn)} THEN NULL ELSE {alias}.subcategory END)"


def account_condition_sql(account_id: str | None, txn: str = "t") -> tuple[str | None, tuple]:
    """The condition, and its parameters, that limits payments `txn` to one account's, or
    (None, ()) for all accounts. Every figure and list that follows the account filter
    builds it here, so "this account" means one thing: a payment belongs to the account
    of its statement (statements.account_id)."""
    if not account_id:
        return None, ()
    return f"{txn}.statement_id IN (SELECT id FROM statements WHERE account_id = ?)", (account_id,)


def account_scope_sql(account_id: str | None, txn: str = "t") -> tuple[str, tuple]:
    """account_condition_sql as " AND ..." (or nothing), for appending to a WHERE clause."""
    condition, params = account_condition_sql(account_id, txn)
    return (f" AND {condition}", params) if condition else ("", ())


# A window that holds every account's payments, for windows_condition_sql.
ALL_ACCOUNTS = "*"


def windows_condition_sql(windows: list[tuple[str | None, str, str]], txn: str = "t") -> tuple[str, tuple]:
    """The condition, and its parameters, that limits payments `txn` to a set of windows,
    each (account_id, first_day, last_day): the payments of that account dated in the range.
    The account is ALL_ACCOUNTS for every account, or None for the statements that belong to
    no account. No windows match nothing.

    The one way a comparison is cut: each account has its own window (the same days of the
    previous month as it is covered to this month), and the figures on both sides of a delta
    are summed over those windows, so they are over the same accounts and the same days."""
    if not windows:
        return "0", ()
    parts, params = [], []
    for account_id, first, last in windows:
        part = f"(date({txn}.txn_date) BETWEEN ? AND ?"
        params += [first, last]
        if account_id == ALL_ACCOUNTS:
            part += ")"
        elif account_id is None:
            part += f" AND {txn}.statement_id IN (SELECT id FROM statements WHERE account_id IS NULL))"
        else:
            part += f" AND {txn}.statement_id IN (SELECT id FROM statements WHERE account_id = ?))"
            params.append(account_id)
        parts.append(part)
    return "(" + " OR ".join(parts) + ")", tuple(params)
