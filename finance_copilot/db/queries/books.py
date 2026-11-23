"""The books: one row per account, one column per month, a gap visible as a gap.

For each account and month the grid says how many payments the account has in it, which
statements cover it, how much of the month they cover (db/queries/coverage.py, the same
definition the Money Map uses for a complete month) and whether the running balances of
those statements check out (the check stored with each statement when it was added). A
month between an account's first and last month that no statement touches is a gap;
months before its first statement and after its last are not cells at all.
"""
from __future__ import annotations

import calendar
import sqlite3

from finance_copilot.db.queries.accounts import account_labels, label_for_statement, list_accounts
from finance_copilot.db.queries.coverage import covered_ranges, statement_spans, touching
from finance_copilot.db.queries.transactions import statement_balance_checks

# How many months that hold a statement the grid shows, newest first.
MONTHS_SHOWN = 12


def _month_of(day: str) -> str:
    return day[:7]


def _next(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year + 1}-01" if mon == 12 else f"{year}-{mon + 1:02d}"


def _bounds(month: str) -> tuple[str, str]:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{month}-01", f"{month}-{calendar.monthrange(year, mon)[1]:02d}"


def _months_between(first: str, last: str) -> list[str]:
    out = [first]
    while out[-1] < last:
        out.append(_next(out[-1]))
    return out


def _span_months(span: dict) -> list[str]:
    return _months_between(_month_of(span["start"]), _month_of(span["end"]))


def books(conn: sqlite3.Connection) -> dict:
    """{months, accounts, transfers}: see the module docstring. Months are oldest first."""
    spans = statement_spans(conn)
    touched: dict[str | None, set[str]] = {
        account: {m for span in items for m in _span_months(span)} for account, items in spans.items()
    }
    held = sorted({m for months in touched.values() for m in months})
    if not held:
        return {"months": [], "accounts": [], "transfers": []}
    shown = held[-MONTHS_SHOWN:]
    months = _months_between(shown[0], shown[-1])

    payments: dict[tuple[str | None, str], int] = {
        (r["account_id"], r["month"]): r["n"]
        for r in conn.execute(
            "SELECT s.account_id AS account_id, strftime('%Y-%m', t.txn_date) AS month, COUNT(*) AS n "
            "FROM transactions t JOIN statements s ON s.id = t.statement_id GROUP BY 1, 2"
        )
    }
    checks = statement_balance_checks(conn)

    accounts = []
    for account in list_accounts(conn):
        items = spans.get(account["id"], [])
        mine = touched.get(account["id"], set())
        if not mine:
            continue
        first_month, last_month = min(mine), max(mine)
        cells: dict[str, dict] = {}
        for month in months:
            if not first_month <= month <= last_month:
                continue
            first, last = _bounds(month)
            found = touching(items, first, last)
            if not found:
                cells[month] = {"gap": True, "payments": payments.get((account["id"], month), 0)}
                continue
            checked = sum(checks.get(s["id"], {}).get("checked", 0) for s in found)
            consistent = sum(checks.get(s["id"], {}).get("consistent", 0) for s in found)
            ranges = covered_ranges(items, first, last)
            # A statement's balance check is of its whole file, so a file that spans several months
            # repeats in each: each statement's check rides with the months of its file, for the
            # page to word it as that statement's.
            statement_checks = []
            for s in found:
                months_of_file = _span_months(s)
                statement_checks.append({
                    "checked": checks.get(s["id"], {}).get("checked", 0),
                    "consistent": checks.get(s["id"], {}).get("consistent", 0),
                    "months": [months_of_file[0], months_of_file[-1]] if len(months_of_file) > 1 else None,
                })
            cells[month] = {
                "gap": False,
                "payments": payments.get((account["id"], month), 0),
                "statement_ids": [s["id"] for s in found],
                "covered": [list(r) for r in ranges],
                "full": ranges == [(first, last)],
                "balance_check": {"checked": checked, "consistent": consistent},
                "statement_checks": statement_checks,
            }
        accounts.append({**account, "cells": cells})
    accounts.sort(key=lambda a: a["account_label"].lower())

    return {"months": months, "accounts": accounts, "transfers": _transfers(conn, months)}


def _label(labels: dict[str, str], row: sqlite3.Row, side: str) -> str:
    return label_for_statement(labels, {
        "account_id": row[f"acc_{side}"], "bank_name": row[f"bank_{side}"], "account_ref": row[f"ref_{side}"],
    })


def _transfers(conn: sqlite3.Connection, months: list[str]) -> list[dict]:
    """The moves between the user's own accounts in the months shown, newest first: each
    one a pair (a debit and a credit linked as a transfer), which no figure counts."""
    labels = account_labels(conn)
    rows = conn.execute(
        """
        SELECT l.txn_a, l.txn_b,
               a.txn_date AS date_a, a.amount AS amount, a.debit_credit AS dc_a,
               sa.account_id AS acc_a, sa.bank_name AS bank_a, sa.account_ref AS ref_a,
               b.txn_date AS date_b,
               sb.account_id AS acc_b, sb.bank_name AS bank_b, sb.account_ref AS ref_b
        FROM transaction_links l
        JOIN transactions a ON a.id = l.txn_a JOIN statements sa ON sa.id = a.statement_id
        JOIN transactions b ON b.id = l.txn_b JOIN statements sb ON sb.id = b.statement_id
        WHERE l.link_type = 'transfer'
        """
    ).fetchall()
    out = []
    for r in rows:
        debit, credit = ("a", "b") if r["dc_a"] == "debit" else ("b", "a")
        date = r[f"date_{debit}"]
        if _month_of(date) not in months:
            continue
        out.append({
            "transaction_id": r[f"txn_{debit}"],
            "txn_date": date,
            "arrived": r[f"date_{credit}"],
            "amount": r["amount"],
            "from_label": _label(labels, r, debit),
            "to_label": _label(labels, r, credit),
        })
    out.sort(key=lambda t: t["txn_date"], reverse=True)
    return out
