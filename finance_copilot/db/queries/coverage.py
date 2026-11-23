"""Which days each account's statements cover.

One definition, read by the Money Map and Dashboard (is this month complete, and can it be
compared with the last one?) and by the books grid (what does each account's month hold?).

Only a printed period is exact. A statement that printed none (every ledger upgraded from
0.2.0) covers from the 1st of its first payment's month, or from the day after the account's
previous statement ends if that is later, to its last payment, and, because the account's
next statement exists, runs on to the end of that month (or the day before the next statement
begins, if sooner): a month followed by later data was not left open just because nothing was
paid after the 28th or before the 3rd. It never runs past its month, so a missing month between
two statements still shows. A printed period is never stretched, so a real gap between two
statements shows as one; it is only widened to hold the statement's own payments, which a
header that excludes them cannot be right about.

An account's coverage is the union of its statements' days. An account with no statement
touching a month has nothing to cover in it, so it never holds that month back. A month whose
coverage starts after its 1st, in an account that has an earlier statement, has a hole at its
start: the days before it are missing, not in the future.
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date, timedelta

_SPANS_SQL = """
SELECT s.id AS id, s.account_id AS account_id,
       COALESCE(s.printed_start, s.period_start, MIN(date(t.txn_date))) AS start,
       COALESCE(s.printed_end, s.period_end, MAX(date(t.txn_date))) AS end,
       MIN(date(t.txn_date)) AS first_payment, MAX(date(t.txn_date)) AS last_payment,
       s.printed_end IS NOT NULL AS printed
FROM statements s
LEFT JOIN transactions t ON t.statement_id = s.id
GROUP BY s.id
"""


def _month_start(day: str) -> str:
    return day[:8] + "01"


def statement_spans(conn: sqlite3.Connection, account_id: str | None = None) -> dict[str | None, list[dict]]:
    """{account id: [{id, start, end, printed}, ...]}, oldest first, for every statement
    that covers any days (or for one account's). A statement with no period and no payments
    covers nothing. A statement is read in the context of its account's other statements,
    so the whole account is read even when only some of its days are asked about."""
    spans: dict[str | None, list[dict]] = {}
    for row in conn.execute(_SPANS_SQL):
        if not (row["start"] and row["end"] and row["start"] <= row["end"]):
            continue
        start, end = row["start"], row["end"]
        if row["first_payment"]:
            # A header cannot be right about days it excludes from its own payments.
            start, end = min(start, row["first_payment"]), max(end, row["last_payment"])
        spans.setdefault(row["account_id"], []).append(
            {"id": row["id"], "start": start, "end": end, "printed": bool(row["printed"])}
        )
    for items in spans.values():
        items.sort(key=lambda s: (s["start"], s["end"]))
        # Ends first, from the statements as they were first read: an unprinted statement runs
        # on to the end of its month when a later one follows.
        starts = [s["start"] for s in items]
        for span in items:
            if span["printed"]:
                continue
            later = [d for d in starts if d > span["end"]]
            if later:
                end = date.fromisoformat(span["end"])
                month_end = end.replace(day=calendar.monthrange(end.year, end.month)[1])
                reaches = min(month_end, date.fromisoformat(min(later)) - timedelta(days=1))
                span["end"] = max(span["end"], reaches.isoformat())
        # Then starts: the 1st of the first payment's month, or the day after the account's
        # previous statement ends, whichever is later, and never after the payment itself.
        previous_end = None
        for span in items:
            if not span["printed"]:
                begins = _month_start(span["start"])
                if previous_end is not None:
                    begins = max(begins, (date.fromisoformat(previous_end) + timedelta(days=1)).isoformat())
                span["start"] = min(span["start"], begins)
            previous_end = span["end"] if previous_end is None else max(previous_end, span["end"])
    if account_id is not None:
        return {account_id: spans[account_id]} if account_id in spans else {}
    return spans


def touching(spans: list[dict], first: str, last: str) -> list[dict]:
    """The statements that cover a day of [first, last]."""
    return [s for s in spans if s["start"] <= last and s["end"] >= first]


def covered_ranges(spans: list[dict], first: str, last: str) -> list[tuple[str, str]]:
    """The days of [first, last] the statements cover, as merged (start, end) ranges."""
    merged: list[tuple[str, str]] = []
    for start, end in sorted((max(s["start"], first), min(s["end"], last)) for s in touching(spans, first, last)):
        if merged and date.fromisoformat(start) <= date.fromisoformat(merged[-1][1]) + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def month_coverage(spans: list[dict], first: str, last: str) -> dict | None:
    """How one account's statements cover the month [first, last], or None when none touches it.

    - ranges: the days covered, as merged (start, end) ranges;
    - from, through: the first day covered and the last day of the first unbroken stretch;
    - earlier: whether the account has a statement that starts before the month;
    - hole_at_start: an earlier statement exists and the first stretch does not start on the
      1st, so the days before it are missing (a month for an account that only began that
      month has no such hole);
    - gap: the first stretch of days missing between the 1st (or the first stretch) and the
      last covered day, as (first_day, last_day), or None. The days after the last covered
      day are not a gap: the month is only in progress, or not yet imported.
    """
    ranges = covered_ranges(spans, first, last)
    if not ranges:
        return None
    earlier = any(s["start"] < first for s in spans)
    hole_at_start = earlier and ranges[0][0] > first
    gap = None
    if hole_at_start:
        gap = (first, (date.fromisoformat(ranges[0][0]) - timedelta(days=1)).isoformat())
    elif len(ranges) > 1:
        gap = (
            (date.fromisoformat(ranges[0][1]) + timedelta(days=1)).isoformat(),
            (date.fromisoformat(ranges[1][0]) - timedelta(days=1)).isoformat(),
        )
    return {
        "ranges": ranges,
        "from": ranges[0][0],
        "through": ranges[0][1],
        "earlier": earlier,
        "hole_at_start": hole_at_start,
        "gap": gap,
    }
