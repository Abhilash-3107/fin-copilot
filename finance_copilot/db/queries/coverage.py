"""Which days of the ledger each account's statements cover.

One definition, read by the Money Map and Dashboard (is this month complete?) and by the
books grid (what does each account's month hold?).

A statement covers the days its header prints (printed_start to printed_end). A statement
that printed none covers the days from its first to its last payment, and, because the
account's next statement exists, runs on to the end of that month (or the day before the
next statement begins, if sooner): a month followed by later data was not left open just
because nothing was paid after the 28th. It never runs past its month, so a missing month
between two statements still shows.
A printed period is exact and is never stretched, so a real gap between two statements
shows as one.

An account's coverage is the union of its statements' days. An account with no statement
touching a month has nothing to cover in it, so it never holds that month back.
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date, timedelta

_SPANS_SQL = """
SELECT s.id AS id, s.account_id AS account_id,
       COALESCE(s.printed_start, s.period_start, MIN(date(t.txn_date))) AS start,
       COALESCE(s.printed_end, s.period_end, MAX(date(t.txn_date))) AS end,
       s.printed_end IS NOT NULL AS printed
FROM statements s
LEFT JOIN transactions t ON t.statement_id = s.id
{where}
GROUP BY s.id
"""


def statement_spans(conn: sqlite3.Connection, account_id: str | None = None) -> dict[str | None, list[dict]]:
    """{account id: [{id, start, end, printed}, ...]}, oldest first, for every statement
    that covers any days (or for one account's). A statement with no period and no payments
    covers nothing. A statement is read in the context of its account's other statements,
    so the whole account is read even when only some of its days are asked about."""
    spans: dict[str | None, list[dict]] = {}
    for row in conn.execute(_SPANS_SQL.format(where="")):
        if row["start"] and row["end"] and row["start"] <= row["end"]:
            spans.setdefault(row["account_id"], []).append(
                {"id": row["id"], "start": row["start"], "end": row["end"], "printed": bool(row["printed"])}
            )
    for items in spans.values():
        items.sort(key=lambda s: (s["start"], s["end"]))
        for span in items:
            if span["printed"]:
                continue
            later = [s["start"] for s in items if s["start"] > span["end"]]
            if later:
                end = date.fromisoformat(span["end"])
                month_end = end.replace(day=calendar.monthrange(end.year, end.month)[1])
                reaches = min(month_end, date.fromisoformat(min(later)) - timedelta(days=1))
                span["end"] = max(span["end"], reaches.isoformat())
    if account_id is not None:
        return {account_id: spans[account_id]} if account_id in spans else {}
    return spans


def touching(spans: list[dict], first: str, last: str) -> list[dict]:
    """The statements that cover a day of [first, last]."""
    return [s for s in spans if s["start"] <= last and s["end"] >= first]


def covered_through(spans: list[dict], first: str, last: str) -> str | None:
    """The last day of [first, last] the account's statements cover, or None when none
    touches it."""
    found = touching(spans, first, last)
    return max(min(s["end"], last) for s in found) if found else None


def covered_ranges(spans: list[dict], first: str, last: str) -> list[tuple[str, str]]:
    """The days of [first, last] the statements cover, as merged (start, end) ranges."""
    merged: list[tuple[str, str]] = []
    for start, end in sorted((max(s["start"], first), min(s["end"], last)) for s in touching(spans, first, last)):
        if merged and date.fromisoformat(start) <= date.fromisoformat(merged[-1][1]) + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
