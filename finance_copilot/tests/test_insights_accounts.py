"""Insights for one account and for all (R3 part D).

`summarize_insights(conn, month, account_id=...)` computes every figure over the
payments of one account, with the same definitions as for all accounts: the one
effective category (an open own-account transfer candidate counts as Uncategorized),
the same offsets and the same breakdown. The identities J2 checks must hold for each
account and for all, and the accounts' figures must add up to the figures of all.

The partial-month rule across accounts is tested here too: a month is complete only
when every account that has a statement touching it is covered to its last day.
"""
from __future__ import annotations

import random
import sqlite3

import pytest
import ulid
from fastapi.testclient import TestClient

from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.accounts import create_account
from finance_copilot.db.queries.insights import summarize_insights
from finance_copilot.db.queries.transactions import (
    list_transaction_facets,
    list_transactions,
    summarize_transactions,
)


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


@pytest.fixture
def conn():
    conn = make_conn()
    yield conn
    conn.close()


def add_account(conn, key, bank="kotak", ref=None, name=None):
    account_id = create_account(conn, bank, ref or f"XXXX{key}", name)
    conn.execute(
        "INSERT INTO statements (id, bank_name, account_ref, parser_version, statement_month, account_id) "
        "VALUES (?, ?, ?, '1', '2026-01', ?)",
        (f"st_{key}", bank, ref or f"XXXX{key}", account_id),
    )
    return account_id


def add_statement(conn, statement_id, account_id, start=None, end=None, printed_start=None, printed_end=None):
    bank, ref = conn.execute("SELECT bank_name, account_ref FROM accounts WHERE id = ?", (account_id,)).fetchone()
    conn.execute(
        "INSERT INTO statements (id, bank_name, account_ref, parser_version, statement_month, account_id, "
        "period_start, period_end, printed_start, printed_end) VALUES (?, ?, ?, '1', ?, ?, ?, ?, ?, ?)",
        (statement_id, bank, ref, (start or "2026-01-01")[:7], account_id, start, end, printed_start, printed_end),
    )


def seed(conn, stmt, date, amount, dc, category=None, subcategory=None, counterparty=None, source="manual", merchant=None):
    txn_id = str(ulid.ULID())
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
        "VALUES (?, ?, ?, ?, ?, 'X', ?)",
        (txn_id, stmt, date, amount, dc, counterparty),
    )
    if category:
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, subcategory, merchant, confidence, source) "
            "VALUES (?, ?, ?, ?, ?, 0.9, ?)",
            (str(ulid.ULID()), txn_id, category, subcategory, merchant, source),
        )
    return txn_id


def link(conn, a, b, link_type="refund"):
    x, y = sorted([a, b])
    conn.execute(
        "INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES (?, ?, ?, ?)",
        (str(ulid.ULID()), x, y, link_type),
    )


def group(conn, members):
    gid = str(ulid.ULID())
    conn.execute("INSERT INTO transaction_groups (id, name) VALUES (?, 'g')", (gid,))
    for txn_id, txn_type in members:
        conn.execute(
            "INSERT INTO transaction_group_members (group_id, transaction_id, txn_type) VALUES (?, ?, ?)",
            (gid, txn_id, txn_type),
        )


def cents(x):
    return round(x, 2)


CATEGORIES = [
    None, None, "Food & Dining", "Shopping", "Transfers", "Self Transfers", "Investments",
    "Income", "Uncategorized", "Miscellaneous", "Entertainment", "Health", "Travel",
]
SOURCES = ["manual", "rule", "llm", "learned_rule", "imported", "user_rule", "pair"]
AMOUNT_POOL = [250.0, 999.0, 1200.0, 2500.0, 4000.0, 5000.0]


def three_account_ledger(conn, seed_value):
    """Random payments over three accounts, every mechanism crossing account lines: refunds,
    reimbursements and splits link a credit in one account to a debit in another, groups
    gather members of several, and repeated amounts a day or two apart make open
    own-account candidates. Returns the account ids."""
    rng = random.Random(seed_value)
    accounts = [add_account(conn, key, bank) for key, bank in (("a", "kotak"), ("b", "sbi"), ("c", "hdfc"))]
    stmts = ["st_a", "st_b", "st_c"]
    ids = {"debit": [], "credit": []}
    for _ in range(120):
        dc = rng.choice(["debit", "debit", "credit"])
        category = rng.choice(CATEGORIES)
        subcategory = None
        if category == "Income":
            subcategory = rng.choice(["Salary", "Refund", "Opening Balance", None])
        elif category == "Transfers":
            subcategory = rng.choice(["ATM Withdrawal", None])
        day = rng.randint(0, 79)
        month, d = divmod(day, 28)
        amount = rng.choice(AMOUNT_POOL) if rng.random() < 0.4 else round(rng.uniform(1, 6000), 2)
        txn = seed(
            conn, rng.choice(stmts), f"2026-{month + 1:02d}-{d + 1:02d}", amount, dc,
            category=category, subcategory=subcategory,
            counterparty=rng.choice([None, "ALPHA", "BETA", "GAMMA"]),
            source=rng.choice(SOURCES),
        )
        ids[dc].append(txn)
    for _ in range(10):
        link(conn, rng.choice(ids["credit"]), rng.choice(ids["debit"]), rng.choice(["refund", "reimbursement", "split", "transfer"]))
    for _ in range(3):
        group(conn, [(i, "split") for i in rng.sample(ids["debit"], 2)] + [(i, "split") for i in rng.sample(ids["credit"], 2)])
    return accounts


def months_of(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT strftime('%Y-%m', txn_date) FROM transactions ORDER BY 1")]


SEEDS = [pytest.param(s, id=f"ledger-{s}") for s in range(25)]


@pytest.mark.parametrize("seed_value", SEEDS)
class TestIdentitiesHoldForAllAndForEachAccount:
    def _views(self, conn, seed_value):
        accounts = three_account_ledger(conn, seed_value)
        for month in months_of(conn):
            for account_id in (None, *accounts):
                yield month, account_id, summarize_insights(conn, month, account_id=account_id)

    def test_kept_is_in_minus_out_minus_invested(self, conn, seed_value):
        for _month, _acct, s in self._views(conn, seed_value):
            v = s["verdict"]
            assert cents(v["money_in"] - v["money_out"] - v["invested"]) == v["kept"]
            if v["prev"]:
                p = v["prev"]
                assert cents(p["money_in"] - p["money_out"] - p["invested"]) == p["kept"]

    def test_the_breakdown_closes_on_out(self, conn, seed_value):
        for month, acct, s in self._views(conn, seed_value):
            rows = sum(c["net"] for c in s["categories"])
            extras = sum(e["total"] for e in s["breakdown"]["extras"])
            assert s["breakdown"]["total"] == s["verdict"]["money_out"]
            assert cents(rows + extras) == s["verdict"]["money_out"], (month, acct)

    def test_category_net_is_gross_minus_offsets(self, conn, seed_value):
        for _month, _acct, s in self._views(conn, seed_value):
            for c in s["categories"]:
                assert cents(c["gross"] - c["offsets"]) == c["net"]

    def test_the_unsorted_figure_the_row_and_the_drill_through_are_the_same_rows(self, conn, seed_value):
        for month, acct, s in self._views(conn, seed_value):
            row = next((c for c in s["categories"] if c["category"] == "Uncategorized"), None)
            drill = summarize_transactions(conn, month=month, categories=["Uncategorized"], account_id=acct)
            assert drill["money_out"] == s["unexplained"]["total"] == (row["gross"] if row else 0), (month, acct)
            listed = list_transactions(conn, month=month, categories=["Uncategorized"], account_id=acct)
            assert sum(1 for t in listed if t["debit_credit"] == "debit") == s["unexplained"]["count"]

    def test_the_recurring_header_equals_its_rows(self, conn, seed_value):
        for _month, _acct, s in self._views(conn, seed_value):
            monthly = [r for r in s["recurring"] if r["active"] and r["cadence"] == "monthly"]
            assert s["recurring_summary"] == {"monthly_total": sum(r["amount"] for r in monthly), "count": len(monthly)}

    def test_the_comparison_is_null_together(self, conn, seed_value):
        for _month, _acct, s in self._views(conn, seed_value):
            has = s["comparison"] is not None
            assert (s["verdict"]["prev"] is not None) is has
            assert all((c["delta"] is not None) is has for c in s["categories"])
            if not has:
                assert s["what_changed"] == []

    def test_the_drill_through_of_each_money_figure_totals_the_figure(self, conn, seed_value):
        for month, acct, s in self._views(conn, seed_value):
            everything = summarize_transactions(conn, month=month, account_id=acct)
            # What came in and went out of the account's payments, before any category is left out,
            # is at least the verdict's cash view: the verdict only leaves Self Transfers and
            # Investments out of Out, and Self Transfers and Opening Balance out of In.
            assert everything["money_out"] >= s["verdict"]["money_out"]
            assert everything["money_in"] >= s["verdict"]["money_in"]

    def test_the_accounts_add_up_to_all_of_them(self, conn, seed_value):
        accounts = three_account_ledger(conn, seed_value)
        for month in months_of(conn):
            whole = summarize_insights(conn, month)
            parts = [summarize_insights(conn, month, account_id=a) for a in accounts]
            for key in ("money_in", "money_out", "invested", "kept", "earned"):
                assert cents(sum(p["verdict"][key] for p in parts)) == whole["verdict"][key], (month, key)
            assert cents(sum(p["unexplained"]["total"] for p in parts)) == whole["unexplained"]["total"]
            assert sum(p["unexplained"]["count"] for p in parts) == whole["unexplained"]["count"]
            net = {}
            for p in parts:
                for c in p["categories"]:
                    net[c["category"]] = net.get(c["category"], 0) + c["net"]
            assert {k: cents(v) for k, v in net.items()} == {c["category"]: c["net"] for c in whole["categories"]}
            assert sum(p["annotation"]["total"] for p in parts) == whole["annotation"]["total"]

    def test_the_people_ledger_adds_up(self, conn, seed_value):
        accounts = three_account_ledger(conn, seed_value)
        for name in ("alpha", "beta", "gamma"):
            conn.execute("INSERT INTO people (id, name, upi, relationship) VALUES (?, ?, ?, 'friend')", (f"p_{name}", name, name))
        for month in months_of(conn):
            whole = summarize_insights(conn, month)["people"]
            parts = [summarize_insights(conn, month, account_id=a)["people"] for a in accounts]
            for key in ("sent", "received", "count"):
                assert cents(sum(p["unmatched"][key] for p in parts)) == whole["unmatched"][key], (month, key)
                for item in whole["items"]:
                    got = sum(i[key] for p in parts for i in p["items"] if i["id"] == item["id"])
                    assert cents(got) == item[key], (month, item["id"], key)

    def test_the_comparison_adds_up_too(self, conn, seed_value):
        """When all accounts are compared, each account is compared too, over its own windows, so
        the previous month and every category's previous net and delta are sums over the accounts."""
        accounts = three_account_ledger(conn, seed_value)
        for month in months_of(conn):
            whole = summarize_insights(conn, month)
            if whole["comparison"] is None:
                assert whole["verdict"]["prev"] is None and whole["what_changed"] == []
                continue
            parts = [summarize_insights(conn, month, account_id=a) for a in accounts]
            held = [p["verdict"]["prev"] for p in parts if p["verdict"]["prev"] is not None]
            assert held, month
            for key in ("money_in", "money_out", "invested", "kept", "earned"):
                assert cents(sum(v[key] for v in held)) == whole["verdict"]["prev"][key], (month, key)
            # A category is a row only where it has spending this month, so a row's previous net and
            # delta add up over the accounts that list it; the categories every account lists are checked.
            for field in ("prev_net", "delta"):
                for c in whole["categories"]:
                    rows = [next((r for r in p["categories"] if r["category"] == c["category"]), None) for p in parts]
                    if None in rows or c[field] is None:
                        continue
                    assert cents(sum(r[field] or 0 for r in rows)) == c[field], (month, c["category"], field)

    def test_every_delta_is_this_month_minus_the_previous_to_the_paisa(self, conn, seed_value):
        accounts = three_account_ledger(conn, seed_value)
        for month in months_of(conn):
            for account_id in (None, *accounts):
                s = summarize_insights(conn, month, account_id=account_id)
                if s["comparison"] is None:
                    continue
                assert s["verdict"]["prev"] is not None and s["comparison_note"] == []
                for c in s["categories"]:
                    assert c["delta"] == cents(c["net"] - c["prev_net"]), (month, account_id, c["category"])
                for item in s["what_changed"]:
                    assert item["delta"] == cents(item["current"] - item["previous"])

    def test_every_payment_is_in_one_account_filter(self, conn, seed_value):
        accounts = three_account_ledger(conn, seed_value)
        whole = summarize_transactions(conn)
        parts = [summarize_transactions(conn, account_id=a) for a in accounts]
        assert sum(p["count"] for p in parts) == whole["count"]
        assert cents(sum(p["money_out"] for p in parts)) == whole["money_out"]


class TestTheFilterIsOneDefinition:
    def test_an_open_candidate_counts_as_uncategorized_in_the_account_view_too(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        out = seed(conn, "st_a", "2026-10-05", 5000, "debit", category="Transfers", subcategory="Peer Transfer", source="rule")
        arrived = seed(conn, "st_b", "2026-10-06", 5000, "credit", category="Income", subcategory="Salary", source="rule")
        seed(conn, "st_a", "2026-10-01", 100, "debit", category="Food & Dining")
        for account_id in (a, None):
            s = summarize_insights(conn, "2026-10", account_id=account_id)
            # The Transfers guess is held back: the money is Uncategorized, a row of its own, in the account view and in all.
            assert next(c for c in s["categories"] if c["category"] == "Uncategorized")["net"] == 5000.0, account_id
        mine = summarize_insights(conn, "2026-10", account_id=a)
        assert mine["unexplained"] == {"count": 1, "total": 5000.0}
        assert mine["verdict"]["money_out"] == 5100.0
        theirs = summarize_insights(conn, "2026-10", account_id=b)
        assert theirs["unexplained"] == {"count": 0, "total": 0}
        assert theirs["verdict"]["money_in"] == 5000.0
        assert out and arrived

    def test_a_paired_transfer_is_in_neither_account_figure(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        out = seed(conn, "st_a", "2026-10-05", 5000, "debit", category="Self Transfers", source="pair")
        arrived = seed(conn, "st_b", "2026-10-05", 5000, "credit", category="Self Transfers", source="pair")
        link(conn, out, arrived, "transfer")
        seed(conn, "st_a", "2026-10-06", 70, "debit", category="Food & Dining")
        for account_id in (a, b):
            v = summarize_insights(conn, "2026-10", account_id=account_id)["verdict"]
            assert v["money_in"] == 0 and v["invested"] == 0
        assert summarize_insights(conn, "2026-10", account_id=a)["verdict"]["money_out"] == 70
        assert summarize_insights(conn, "2026-10", account_id=b)["verdict"]["money_out"] == 0


class TestOffsetsLandInTheAccountOfTheDebitTheyPayBack:
    """A refund, a group share or a matched credit takes spending off in the account view of the
    debit it pays back, on the day the credit arrived, in that debit's category. In all accounts
    that is the same figure as before. The credit itself still counts in its own account's In.
    Each view's breakdown closes on its Out."""

    def _refund_across_accounts(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        shoes = seed(conn, "st_a", "2026-10-05", 3000, "debit", category="Shopping")
        refund = seed(conn, "st_b", "2026-10-20", 3000, "credit", category="Income", subcategory="Refund")
        link(conn, shoes, refund, "refund")
        seed(conn, "st_a", "2026-10-07", 400, "debit", category="Food & Dining")
        return a, b

    @staticmethod
    def _shopping(s):
        return next((c for c in s["categories"] if c["category"] == "Shopping"), None)

    def test_all_accounts_net_the_refund_off_shopping(self, conn):
        self._refund_across_accounts(conn)
        shopping = self._shopping(summarize_insights(conn, "2026-10"))
        assert (shopping["gross"], shopping["offsets"], shopping["net"]) == (3000, 3000, 0)

    def test_the_account_that_paid_nets_the_refund_and_closes(self, conn):
        a, _b = self._refund_across_accounts(conn)
        s = summarize_insights(conn, "2026-10", account_id=a)
        shopping = self._shopping(s)
        assert (shopping["gross"], shopping["offsets"], shopping["net"]) == (3000, 3000, 0)
        assert cents(sum(c["net"] for c in s["categories"]) + sum(e["total"] for e in s["breakdown"]["extras"])) == s["verdict"]["money_out"] == 3400

    def test_the_account_that_received_it_has_no_category_row_for_it_and_still_counts_it_in(self, conn):
        _a, b = self._refund_across_accounts(conn)
        s = summarize_insights(conn, "2026-10", account_id=b)
        assert self._shopping(s) is None, "no negative row with nothing behind it"
        assert s["categories"] == []
        assert s["breakdown"]["extras"] == []
        assert s["verdict"]["money_in"] == 3000 and s["verdict"]["money_out"] == 0

    def test_the_offset_counts_on_the_day_the_credit_arrived(self, conn):
        a, _b = self._refund_across_accounts(conn)
        conn.execute("UPDATE transactions SET txn_date = '2026-11-02' WHERE amount = 3000 AND debit_credit = 'credit'")
        october = self._shopping(summarize_insights(conn, "2026-10", account_id=a))
        assert (october["gross"], october["offsets"]) == (3000, 0)
        november = self._shopping(summarize_insights(conn, "2026-11", account_id=a))
        assert (november["gross"], november["offsets"], november["net"]) == (0, 3000, -3000)

    def test_a_group_share_nets_the_account_of_the_debit(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        concert = seed(conn, "st_a", "2026-10-05", 4000, "debit", category="Entertainment")
        share = seed(conn, "st_b", "2026-10-09", 1000, "credit", category="Transfers")
        group(conn, [(concert, "split"), (share, "split")])
        whole = summarize_insights(conn, "2026-10")
        assert next(c for c in whole["categories"] if c["category"] == "Entertainment")["net"] == 3000
        mine = summarize_insights(conn, "2026-10", account_id=a)
        assert next(c for c in mine["categories"] if c["category"] == "Entertainment")["net"] == 3000
        theirs = summarize_insights(conn, "2026-10", account_id=b)
        assert all(c["category"] != "Entertainment" for c in theirs["categories"])
        assert theirs["verdict"]["money_in"] == 1000

    def test_a_group_share_over_debits_in_two_accounts_is_split_between_them(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        add_account(conn, "c", "hdfc")
        one = seed(conn, "st_a", "2026-10-05", 3000, "debit", category="Entertainment")
        two = seed(conn, "st_b", "2026-10-05", 1000, "debit", category="Entertainment")
        share = seed(conn, "st_c", "2026-10-09", 2000, "credit", category="Transfers")
        group(conn, [(one, "split"), (two, "split"), (share, "split")])
        nets = {acct: next(c for c in summarize_insights(conn, "2026-10", account_id=acct)["categories"] if c["category"] == "Entertainment")["net"] for acct in (a, b)}
        assert nets == {a: 1500, b: 500}

    def test_a_counterparty_matched_refund_nets_the_account_of_the_debit(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        seed(conn, "st_a", "2026-10-05", 800, "debit", category="Shopping", counterparty="AMAZON")
        seed(conn, "st_b", "2026-10-20", 300, "credit", category="Income", subcategory="Refund", counterparty="AMAZON")
        assert self._shopping(summarize_insights(conn, "2026-10", account_id=a))["net"] == 500
        assert self._shopping(summarize_insights(conn, "2026-10", account_id=b)) is None
        assert self._shopping(summarize_insights(conn, "2026-10"))["net"] == 500

    def test_an_amount_matched_refund_nets_the_account_of_the_debit(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        seed(conn, "st_a", "2026-10-05", 800, "debit", category="Shopping")
        seed(conn, "st_b", "2026-10-20", 800, "credit", category="Income", subcategory="Refund")
        assert self._shopping(summarize_insights(conn, "2026-10", account_id=a))["net"] == 0
        assert self._shopping(summarize_insights(conn, "2026-10", account_id=b)) is None

    def test_a_refund_for_the_newest_matching_payment_that_is_unsorted_is_not_put_on_an_older_one(self, conn):
        a = add_account(conn, "a")
        seed(conn, "st_a", "2026-10-02", 800, "debit", category="Shopping")
        seed(conn, "st_a", "2026-10-09", 800, "debit")  # newer, same amount, not sorted
        seed(conn, "st_a", "2026-10-20", 800, "credit", category="Income", subcategory="Refund")
        s = summarize_insights(conn, "2026-10", account_id=a)
        assert self._shopping(s)["offsets"] == 0
        assert next(c for c in s["categories"] if c["category"] == "Uncategorized")["net"] == 800

    def test_a_payee_refund_of_an_exact_amount_goes_to_that_debit_whatever_is_newer_and_unsorted(self, conn):
        # The newest debit of the payee is not sorted, but the refund is of exactly 2000, which an
        # older sorted one was: it nets that one, and sorting the newer one changes nothing.
        a = add_account(conn, "a")
        seed(conn, "st_a", "2026-10-05", 2000, "debit", category="Shopping", counterparty="AMAZON")
        newer = seed(conn, "st_a", "2026-10-20", 500, "debit", category="Uncategorized", counterparty="AMAZON")
        seed(conn, "st_a", "2026-10-25", 2000, "credit", category="Income", subcategory="Refund", counterparty="AMAZON")
        before = self._shopping(summarize_insights(conn, "2026-10", account_id=a))
        assert (before["gross"], before["offsets"], before["net"]) == (2000, 2000, 0)
        conn.execute("UPDATE annotations SET category = 'Food & Dining' WHERE transaction_id = ?", (newer,))
        after = self._shopping(summarize_insights(conn, "2026-10", account_id=a))
        assert (after["offsets"], after["net"]) == (2000, 0)

    def test_a_payee_refund_waits_while_the_debit_it_is_for_is_unsorted(self, conn):
        a = add_account(conn, "a")
        seed(conn, "st_a", "2026-10-05", 2000, "debit", category="Shopping", counterparty="AMAZON")
        newer = seed(conn, "st_a", "2026-10-20", 500, "debit", category="Uncategorized", counterparty="AMAZON")
        seed(conn, "st_a", "2026-10-25", 500, "credit", category="Income", subcategory="Refund", counterparty="AMAZON")
        waiting = summarize_insights(conn, "2026-10", account_id=a)
        assert self._shopping(waiting)["offsets"] == 0
        assert next(c for c in waiting["categories"] if c["category"] == "Uncategorized")["net"] == 500
        conn.execute("UPDATE annotations SET category = 'Food & Dining' WHERE transaction_id = ?", (newer,))
        sorted_now = summarize_insights(conn, "2026-10", account_id=a)
        food = next(c for c in sorted_now["categories"] if c["category"] == "Food & Dining")
        assert (food["gross"], food["offsets"], food["net"]) == (500, 500, 0)
        assert self._shopping(sorted_now)["offsets"] == 0

    def test_a_payee_refund_for_a_purchase_with_no_label_at_all_waits_too(self, conn):
        a = add_account(conn, "a")
        seed(conn, "st_a", "2026-10-05", 2000, "debit", category="Shopping", counterparty="AMAZON")
        seed(conn, "st_a", "2026-10-20", 500, "debit", None, counterparty="AMAZON")
        seed(conn, "st_a", "2026-10-25", 500, "credit", category="Income", subcategory="Refund", counterparty="AMAZON")
        s = summarize_insights(conn, "2026-10", account_id=a)
        assert self._shopping(s)["offsets"] == 0
        assert next(c for c in s["categories"] if c["category"] == "Uncategorized")["net"] == 500

    def test_a_payee_refund_cannot_net_a_debit_below_zero_when_a_link_took_its_capacity(self, conn):
        a = add_account(conn, "a")
        debit = seed(conn, "st_a", "2026-10-05", 1000, "debit", category="Shopping", counterparty="AMAZON")
        linked = seed(conn, "st_a", "2026-10-10", 1000, "credit", category="Income", subcategory="Refund", counterparty="AMAZON")
        link(conn, debit, linked)
        seed(conn, "st_a", "2026-10-12", 1000, "credit", category="Income", subcategory="Refund", counterparty="AMAZON")
        shopping = self._shopping(summarize_insights(conn, "2026-10", account_id=a))
        assert (shopping["gross"], shopping["offsets"], shopping["net"]) == (1000, 1000, 0)

    def test_the_uncategorized_row_never_takes_offsets(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        loose = seed(conn, "st_a", "2026-10-03", 999, "debit")  # not sorted
        seed(conn, "st_a", "2026-10-04", 100, "debit", category="Food & Dining")
        seed(conn, "st_b", "2026-10-09", 999, "credit", category="Income", subcategory="Refund")  # same amount
        paid = seed(conn, "st_b", "2026-10-10", 50, "credit", category="Income", subcategory="Refund")
        link(conn, loose, paid, "refund")
        for account_id in (None, a, b):
            s = summarize_insights(conn, "2026-10", account_id=account_id)
            row = next((c for c in s["categories"] if c["category"] == "Uncategorized"), None)
            figure = s["unexplained"]["total"]
            assert (row["net"] if row else 0) == figure, account_id
            assert (row["offsets"] if row else 0) == 0, account_id

    def test_a_spend_labelled_credit_nets_its_own_accounts_category(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        seed(conn, "st_a", "2026-10-05", 700, "debit", category="Shopping")
        seed(conn, "st_b", "2026-10-06", 200, "credit", category="Shopping")
        assert next(c for c in summarize_insights(conn, "2026-10")["categories"] if c["category"] == "Shopping")["net"] == 500
        assert next(c for c in summarize_insights(conn, "2026-10", account_id=a)["categories"] if c["category"] == "Shopping")["net"] == 700
        assert next(c for c in summarize_insights(conn, "2026-10", account_id=b)["categories"] if c["category"] == "Shopping")["net"] == -200


class TestEveryFigureFollowsTheFilter:
    def test_recurring_merchants_people_and_balance_belong_to_the_account(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        for month in (8, 9, 10):
            seed(conn, "st_a", f"2026-{month:02d}-05", 649, "debit", category="Subscriptions", counterparty="NETFLIX")
            seed(conn, "st_b", f"2026-{month:02d}-09", 119, "debit", category="Subscriptions", counterparty="SPOTIFY")
        seed(conn, "st_a", "2026-10-12", 800, "debit", category="Food & Dining", counterparty="ZOMATO")
        seed(conn, "st_b", "2026-10-13", 300, "debit", category="Food & Dining", counterparty="SWIGGY")
        conn.execute("INSERT INTO people (id, name, upi, relationship) VALUES ('p1', 'neha', 'neha', 'friend')")
        seed(conn, "st_a", "2026-10-14", 440, "debit", category="Transfers", merchant="neha")

        whole = summarize_insights(conn, "2026-10")
        mine = summarize_insights(conn, "2026-10", account_id=a)
        theirs = summarize_insights(conn, "2026-10", account_id=b)

        assert sorted(r["amount"] for r in whole["recurring"]) == [119, 649]
        assert [r["amount"] for r in mine["recurring"]] == [649]
        assert [r["amount"] for r in theirs["recurring"]] == [119]
        assert mine["recurring_summary"]["monthly_total"] == 649 and theirs["recurring_summary"]["monthly_total"] == 119
        assert [m["total"] for m in mine["merchants"]] == [800, 649]
        assert [m["total"] for m in theirs["merchants"]] == [300, 119]
        assert mine["people"]["items"][0]["sent"] == 440 and theirs["people"]["items"] == []
        assert theirs["people"]["unmatched"]["count"] == 0

    def test_the_balance_line_is_the_selected_accounts_and_all_shows_the_busiest(self, conn):
        _, b = add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
        for day in (1, 2, 3):
            conn.execute(
                "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, running_balance) "
                "VALUES (?, 'st_a', ?, 10, 'debit', 'X', ?)", (str(ulid.ULID()), f"2026-10-0{day}", 1000 - day))
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, running_balance) "
            "VALUES (?, 'st_b', '2026-10-02', 10, 'debit', 'X', 50)", (str(ulid.ULID()),))
        whole = summarize_insights(conn, "2026-10")["balance"]
        assert whole["account"] == "Kotak savings" and len(whole["series"]) == 3
        theirs = summarize_insights(conn, "2026-10", account_id=b)["balance"]
        assert theirs == {"account": "SBI savings", "series": [{"date": "2026-10-02", "balance": 50}], "no_balances": False}

    def _balance_rows(self, conn, stmt, days, month="2026-10"):
        for day in days:
            conn.execute(
                "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, running_balance) "
                "VALUES (?, ?, ?, 10, 'debit', 'X', 500)", (str(ulid.ULID()), stmt, f"{month}-{day:02d}"))

    def test_with_all_accounts_the_line_is_the_account_with_most_balance_rows_in_the_selected_month(self, conn):
        add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
        # Kotak has the most balances overall (October), SBI the most in November.
        self._balance_rows(conn, "st_a", (1, 2, 3, 4, 5))
        self._balance_rows(conn, "st_a", (1,), "2026-11")
        self._balance_rows(conn, "st_b", (1, 2), "2026-11")
        assert summarize_insights(conn, "2026-10")["balance"]["account"] == "Kotak savings"
        november = summarize_insights(conn, "2026-11")["balance"]
        assert november["account"] == "SBI savings"
        # The line is still the account's whole history, for the page to narrow to the month.
        assert [p["date"] for p in november["series"]] == ["2026-11-01", "2026-11-02"]

    def test_a_month_no_account_has_balances_in_falls_back_to_the_busiest_over_all_history(self, conn):
        add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
        self._balance_rows(conn, "st_a", (1, 2, 3))
        self._balance_rows(conn, "st_b", (1,))
        seed(conn, "st_b", "2026-11-05", 10, "debit", category="Shopping")
        assert summarize_insights(conn, "2026-11")["balance"]["account"] == "Kotak savings"

    def test_statements_with_no_running_balance_say_so(self, conn):
        add_account(conn, "a", name="Kotak savings")
        seed(conn, "st_a", "2026-10-05", 10, "debit", category="Shopping")
        assert summarize_insights(conn, "2026-10")["balance"] == {"account": None, "series": [], "no_balances": True}

    def test_an_account_with_no_balances_says_which_account_has_none(self, conn):
        _, b = add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
        seed(conn, "st_a", "2026-10-02", 10, "debit", category="Food & Dining")
        assert summarize_insights(conn, "2026-10", account_id=b)["balance"] == {"account": "SBI savings", "series": [], "no_balances": True}

    def test_months_stay_the_ledgers_so_the_period_never_loses_a_month(self, conn):
        _, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        seed(conn, "st_a", "2026-09-05", 10, "debit", category="Food & Dining")
        seed(conn, "st_b", "2026-10-05", 10, "debit", category="Food & Dining")
        s = summarize_insights(conn, "2026-09", account_id=b)
        assert s["months"] == ["2026-09", "2026-10"] and s["month"] == "2026-09"
        assert s["verdict"]["money_out"] == 0 and s["annotation"] == {"total": 0, "annotated": 0}

    def test_the_account_is_named_in_the_answer(self, conn):
        a = add_account(conn, "a", name="Kotak savings")
        seed(conn, "st_a", "2026-10-05", 10, "debit", category="Food & Dining")
        assert summarize_insights(conn, "2026-10", account_id=a)["account"] == {"id": a, "label": "Kotak savings"}
        assert summarize_insights(conn, "2026-10")["account"] is None

    def test_a_ledger_with_no_payments_has_no_account_either(self, conn):
        assert summarize_insights(conn, None, account_id="acc_nobody") == {"months": [], "month": None}


class TestPartialMonthsAcrossAccounts:
    """A month is complete only when every account that has a statement touching it is covered
    to its last day, by the printed period when there is one, else by the last payment."""

    def _two(self, conn, kotak_end="2026-11-30", sbi_end="2026-11-15", sbi_printed=True):
        a, b = add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "k_oct", a, "2026-10-01", "2026-10-31", "2026-10-01", "2026-10-31")
        add_statement(conn, "k_nov", a, "2026-11-02", "2026-11-28", "2026-11-01", kotak_end)
        add_statement(conn, "s_oct", b, "2026-10-02", "2026-10-30", "2026-10-01", "2026-10-31")
        add_statement(conn, "s_nov", b, "2026-11-03", "2026-11-14", "2026-11-01" if sbi_printed else None, sbi_end if sbi_printed else None)
        for stmt, day, amount in (
            ("k_oct", "2026-10-02", 100), ("k_oct", "2026-10-20", 100), ("k_nov", "2026-11-02", 10), ("k_nov", "2026-11-28", 10),
            ("s_oct", "2026-10-02", 50), ("s_oct", "2026-10-20", 50), ("s_nov", "2026-11-03", 5), ("s_nov", "2026-11-14", 5),
        ):
            seed(conn, stmt, day, amount, "debit", category="Shopping")
        return a, b

    @staticmethod
    def _drop(conn, statement_id):
        conn.execute("DELETE FROM annotations WHERE transaction_id IN (SELECT id FROM transactions WHERE statement_id = ?)", (statement_id,))
        conn.execute("DELETE FROM transactions WHERE statement_id = ?", (statement_id,))
        conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))

    def test_one_account_short_makes_the_month_partial_and_names_it(self, conn):
        a, b = self._two(conn)
        s = summarize_insights(conn, "2026-11")
        assert s["period"]["partial"] is True
        assert s["period"]["covered_through"] == "2026-11-15"
        assert s["period"]["counted"] == 2
        assert s["period"]["short"] == [
            {"account_id": b, "account_label": "SBI savings", "covered_from": None, "covered_through": "2026-11-15", "closed": False}
        ]

    def test_each_account_is_compared_over_the_same_days_of_its_own_previous_month(self, conn):
        a, b = self._two(conn)
        s = summarize_insights(conn, "2026-11")
        assert s["comparison"] == {"month": "2026-10", "start": "2026-10-01", "end": "2026-10-31", "uniform": False}
        # Kotak is covered to the 30th: all of October (200). SBI to the 15th: its 2 Oct payment (50), not the 20th.
        assert s["verdict"]["prev"]["money_out"] == 250
        assert s["verdict"]["money_out"] == 30
        shopping = next(c for c in s["categories"] if c["category"] == "Shopping")
        assert (shopping["net"], shopping["prev_net"], shopping["delta"]) == (30, 250, -220)
        kotak = summarize_insights(conn, "2026-11", account_id=a)
        assert kotak["comparison"] == {"month": "2026-10", "start": "2026-10-01", "end": "2026-10-31", "uniform": True}
        assert kotak["verdict"]["prev"]["money_out"] == 200
        sbi = summarize_insights(conn, "2026-11", account_id=b)
        assert sbi["comparison"] == {"month": "2026-10", "start": "2026-10-01", "end": "2026-10-15", "uniform": True}
        assert sbi["verdict"]["prev"]["money_out"] == 50

    def test_a_new_account_means_no_comparison_at_all_and_says_so(self, conn):
        # SBI opened on 10 Nov and spent 5,000 on its first days: nothing of October to compare it
        # with, and a delta over Kotak alone would read as the whole view's.
        a, b = add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "ka", a, "2026-10-01", "2026-11-30", "2026-10-01", "2026-11-30")
        add_statement(conn, "sb", b, "2026-11-10", "2026-11-30", "2026-11-10", "2026-11-30")
        seed(conn, "ka", "2026-10-05", 1000, "debit", category="Food & Dining")
        seed(conn, "ka", "2026-11-05", 1000, "debit", category="Food & Dining")
        seed(conn, "sb", "2026-11-12", 5000, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-11")
        assert s["verdict"]["money_out"] == 6000
        assert s["comparison"] is None and s["verdict"]["prev"] is None
        assert s["comparison_note"] == [{"account_label": "SBI savings", "reason": "was added in November"}]
        assert s["what_changed"] == []
        assert all(c["prev_net"] is None and c["delta"] is None for c in s["categories"])
        sbi = summarize_insights(conn, "2026-11", account_id=b)
        assert sbi["comparison"] is None
        assert sbi["comparison_note"] == [{"account_label": "SBI savings", "reason": "was added in November"}]
        kotak = summarize_insights(conn, "2026-11", account_id=a)
        assert kotak["comparison"] is not None and kotak["comparison_note"] == []
        assert kotak["verdict"]["prev"]["money_out"] == 1000

    def test_an_account_with_no_statement_for_last_month_means_no_comparison_and_says_so(self, conn):
        a, b = add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "k9", a, "2026-09-01", "2026-09-30", "2026-09-01", "2026-09-30")
        add_statement(conn, "k11", a, "2026-11-01", "2026-11-30", "2026-11-01", "2026-11-30")
        add_statement(conn, "s0", b, "2026-09-01", "2026-11-30", "2026-09-01", "2026-11-30")
        seed(conn, "k9", "2026-09-05", 3000, "debit", category="Food & Dining")
        seed(conn, "k11", "2026-11-05", 3000, "debit", category="Food & Dining")
        for month in ("09", "10", "11"):
            seed(conn, "s0", f"2026-{month}-05", 100, "debit", category="Food & Dining")
        s = summarize_insights(conn, "2026-11")
        assert s["comparison"] is None and s["verdict"]["prev"] is None and s["what_changed"] == []
        assert s["comparison_note"] == [{"account_label": "Kotak savings", "reason": "has no statement for October"}]
        assert summarize_insights(conn, "2026-11", account_id=a)["comparison_note"] == s["comparison_note"]
        # SBI's own view is compared: 100 against 100.
        sbi = summarize_insights(conn, "2026-11", account_id=b)
        assert sbi["verdict"]["prev"]["money_out"] == 100 and sbi["comparison_note"] == []

    def test_a_statement_missing_part_of_last_months_window_names_the_days(self, conn):
        a, b = add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "k10", a, "2026-10-01", "2026-10-31", "2026-10-01", "2026-10-31")
        add_statement(conn, "k11", a, "2026-11-01", "2026-11-30", "2026-11-01", "2026-11-30")
        add_statement(conn, "s10", b, "2026-10-01", "2026-10-19", "2026-10-01", "2026-10-19")
        add_statement(conn, "s11", b, "2026-11-01", "2026-11-20", "2026-11-01", "2026-11-20")
        seed(conn, "k10", "2026-10-05", 100, "debit", category="Shopping")
        seed(conn, "k11", "2026-11-05", 100, "debit", category="Shopping")
        seed(conn, "s10", "2026-10-05", 100, "debit", category="Shopping")
        seed(conn, "s11", "2026-11-05", 100, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-11")
        assert s["period"]["short"][0]["covered_through"] == "2026-11-20"
        assert s["comparison"] is None
        assert s["comparison_note"] == [{"account_label": "SBI savings", "reason": "has no statement for 20 Oct"}]
        conn.execute("UPDATE statements SET printed_end = '2026-10-10', period_end = '2026-10-10' WHERE id = 's10'")
        s = summarize_insights(conn, "2026-11")
        assert s["comparison_note"] == [{"account_label": "SBI savings", "reason": "has no statement for 11-20 Oct"}]

    def test_the_first_month_of_a_ledger_has_no_comparison_and_nothing_to_explain(self, conn):
        a, b = add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "ka", a, "2026-11-01", "2026-11-30", "2026-11-01", "2026-11-30")
        add_statement(conn, "sb", b, "2026-11-05", "2026-11-30", "2026-11-05", "2026-11-30")
        seed(conn, "ka", "2026-11-05", 100, "debit", category="Shopping")
        seed(conn, "sb", "2026-11-12", 100, "debit", category="Shopping")
        for acct in (None, a, b):
            s = summarize_insights(conn, "2026-11", account_id=acct)
            assert s["comparison"] is None and s["comparison_note"] == [], acct

    def test_a_covered_window_with_no_payments_is_a_month_of_zero(self, conn):
        a = add_account(conn, "a", name="Kotak savings")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "k10", a, "2026-10-01", "2026-10-31", "2026-10-01", "2026-10-31")
        add_statement(conn, "k11", a, "2026-11-01", "2026-11-30", "2026-11-01", "2026-11-30")
        seed(conn, "k11", "2026-11-05", 100, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-11")
        assert s["comparison"] is not None and s["verdict"]["prev"]["money_out"] == 0
        shopping = next(c for c in s["categories"] if c["category"] == "Shopping")
        assert shopping["delta"] == 100

    def test_a_month_that_starts_with_a_hole_is_partial_and_cannot_be_compared(self, conn):
        # Kotak printed all of October and then 10 to 30 Nov: 1 to 9 Nov is missing.
        a = add_account(conn, "a", name="Kotak savings")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "k10", a, "2026-10-01", "2026-10-31", "2026-10-01", "2026-10-31")
        add_statement(conn, "k11", a, "2026-11-10", "2026-11-30", "2026-11-10", "2026-11-30")
        seed(conn, "k10", "2026-10-05", 100, "debit", category="Shopping")
        seed(conn, "k11", "2026-11-12", 100, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-11")
        assert s["period"]["partial"] is True
        assert s["period"]["short"] == [
            {"account_id": a, "account_label": "Kotak savings", "covered_from": "2026-11-10", "covered_through": "2026-11-30", "closed": False}
        ]
        assert s["comparison"] is None
        assert s["comparison_note"] == [{"account_label": "Kotak savings", "reason": "has no statement for 1-9 Nov"}]

    def test_a_hole_inside_the_month_makes_it_partial_and_there_is_no_comparison(self, conn):
        a = add_account(conn, "a", name="Kotak savings")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "k10", a, "2026-10-01", "2026-10-31", "2026-10-01", "2026-10-31")
        add_statement(conn, "k1", a, "2026-11-01", "2026-11-10", "2026-11-01", "2026-11-10")
        add_statement(conn, "k2", a, "2026-11-20", "2026-11-30", "2026-11-20", "2026-11-30")
        seed(conn, "k10", "2026-10-05", 100, "debit", category="Shopping")
        seed(conn, "k10", "2026-10-25", 100, "debit", category="Shopping")
        seed(conn, "k1", "2026-11-05", 10, "debit", category="Shopping")
        seed(conn, "k2", "2026-11-25", 10, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-11")
        assert s["period"]["partial"] is True and s["period"]["covered_through"] == "2026-11-10"
        assert s["period"]["short"] == [
            {"account_id": a, "account_label": "Kotak savings", "covered_from": None, "covered_through": "2026-11-10", "closed": False}
        ]
        # The 25 Nov payment is past the hole: a delta over 1 to 10 Nov would be wrong, so there is none.
        assert s["comparison"] is None and s["verdict"]["prev"] is None
        assert s["comparison_note"] == [{"account_label": "Kotak savings", "reason": "has no statement for 11-19 Nov"}]
        assert s["verdict"]["money_out"] == 20

    def test_a_closed_account_whose_last_statement_printed_no_period(self, conn):
        # SBI closed on 12 Sep and its last statement printed no period; Kotak carries on.
        a, b = add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "ka", a, "2026-08-01", "2026-10-31", "2026-08-01", "2026-10-31")
        add_statement(conn, "s8", b, "2026-08-01", "2026-08-31", "2026-08-01", "2026-08-31")
        add_statement(conn, "s9", b, "2026-09-02", "2026-09-12")  # no printed period
        for month in ("08", "09", "10"):
            seed(conn, "ka", f"2026-{month}-05", 1000, "debit", category="Food & Dining")
        seed(conn, "s8", "2026-08-03", 100, "debit", category="Shopping")
        seed(conn, "s8", "2026-08-25", 100, "debit", category="Shopping")
        seed(conn, "s9", "2026-09-02", 100, "debit", category="Shopping")
        seed(conn, "s9", "2026-09-12", 100, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-09")
        # The month is partial, and the page can name the closed account: it is covered to the 12th,
        # has no later statement, and the ledger has since moved on.
        assert s["period"]["partial"] is True and s["period"]["covered_through"] == "2026-09-12"
        assert s["period"]["short"] == [
            {"account_id": b, "account_label": "SBI savings", "covered_from": None, "covered_through": "2026-09-12", "closed": True}
        ]
        # Kotak against all of August, SBI against 1 to 12 Aug (its 3 Aug payment only).
        assert s["comparison"] == {"month": "2026-08", "start": "2026-08-01", "end": "2026-08-31", "uniform": False}
        assert s["verdict"]["prev"]["money_out"] == 1100 and s["verdict"]["money_out"] == 1200
        assert [c["category"] for c in s["what_changed"]] == ["Shopping"]
        assert s["what_changed"][0]["delta"] == 100
        sbi = summarize_insights(conn, "2026-09", account_id=b)
        assert sbi["period"]["covered_through"] == "2026-09-12"
        assert sbi["comparison"]["end"] == "2026-08-12"

    def test_with_one_account_selected_only_that_account_counts(self, conn):
        a, b = self._two(conn)
        mine = summarize_insights(conn, "2026-11", account_id=a)
        assert mine["period"]["partial"] is False and mine["period"]["covered_through"] == "2026-11-30"
        assert mine["period"]["short"] == []
        assert mine["comparison"] == {"month": "2026-10", "start": "2026-10-01", "end": "2026-10-31", "uniform": True}
        theirs = summarize_insights(conn, "2026-11", account_id=b)
        assert theirs["period"]["partial"] is True and theirs["period"]["covered_through"] == "2026-11-15"
        assert theirs["comparison"]["end"] == "2026-10-15"

    def test_every_account_covered_to_the_last_day_makes_it_complete(self, conn):
        self._two(conn, sbi_end="2026-11-30")
        s = summarize_insights(conn, "2026-11")
        assert s["period"]["partial"] is False and s["period"]["short"] == []
        assert s["comparison"]["end"] == "2026-10-31"

    def test_a_day_short_is_partial_and_the_last_day_is_not(self, conn):
        self._two(conn, sbi_end="2026-11-29")
        assert summarize_insights(conn, "2026-11")["period"]["covered_through"] == "2026-11-29"
        assert summarize_insights(conn, "2026-11")["period"]["partial"] is True

    def test_an_account_whose_statements_stop_before_the_month_does_not_hold_it_back(self, conn):
        a, b = self._two(conn)
        self._drop(conn, "s_nov")
        s = summarize_insights(conn, "2026-11")
        assert s["period"]["partial"] is False and s["period"]["short"] == []
        assert s["period"]["covered_through"] == "2026-11-30"

    def test_the_printed_period_beats_the_last_payment(self, conn):
        # SBI's last payment is on the 14th but the statement says it runs to the 30th.
        self._two(conn, sbi_end="2026-11-30")
        assert summarize_insights(conn, "2026-11")["period"]["partial"] is False

    def test_without_a_printed_period_the_last_payment_decides(self, conn):
        _a, b = self._two(conn, sbi_printed=False)
        s = summarize_insights(conn, "2026-11")
        assert s["period"]["covered_through"] == "2026-11-14"
        # Kotak is compared over all of October, SBI over 1 to 14 Oct.
        assert s["comparison"]["end"] == "2026-10-31" and s["comparison"]["uniform"] is False
        assert summarize_insights(conn, "2026-11", account_id=b)["comparison"]["end"] == "2026-10-14"

    def test_a_printed_period_is_exact_so_a_real_gap_before_the_next_statement_shows(self, conn):
        a, b = self._two(conn)
        add_statement(conn, "s_dec", b, "2026-12-01", "2026-12-10", "2026-12-01", "2026-12-10")
        seed(conn, "s_dec", "2026-12-02", 5, "debit", category="Shopping")
        # SBI printed 1 to 15 Nov and its next statement starts on 1 Dec: 16 to 30 Nov is a gap.
        s = summarize_insights(conn, "2026-11")
        assert s["period"]["partial"] is True and s["period"]["covered_through"] == "2026-11-15"

    def test_a_statement_with_no_printed_period_is_taken_to_run_to_the_end_of_its_month_when_one_follows(self, conn):
        a, b = self._two(conn, sbi_printed=False)
        assert summarize_insights(conn, "2026-11")["period"]["covered_through"] == "2026-11-14"
        add_statement(conn, "s_dec", b, "2026-12-01", "2026-12-10", "2026-12-01", "2026-12-10")
        seed(conn, "s_dec", "2026-12-02", 5, "debit", category="Shopping")
        # Its last payment was on the 14th and nothing says more, but December follows.
        s = summarize_insights(conn, "2026-11")
        assert s["period"]["partial"] is False and s["period"]["short"] == []

    def test_a_missing_month_between_two_statements_is_not_covered_by_the_one_before(self, conn):
        a, _ = add_account(conn, "a"), add_account(conn, "b", "sbi")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "jan", a, "2026-01-02", "2026-01-28")
        add_statement(conn, "apr", a, "2026-04-02", "2026-04-28")
        seed(conn, "jan", "2026-01-02", 10, "debit", category="Shopping")
        seed(conn, "jan", "2026-01-28", 10, "debit", category="Shopping")
        seed(conn, "apr", "2026-04-02", 10, "debit", category="Shopping")
        seed(conn, "apr", "2026-04-28", 10, "debit", category="Shopping")
        assert summarize_insights(conn, "2026-01")["period"]["partial"] is False
        assert summarize_insights(conn, "2026-04")["period"]["partial"] is True

    def test_a_month_in_a_leap_february_compares_with_the_29th(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "ka", a, "2028-02-01", "2028-03-31", "2028-02-01", "2028-03-31")
        add_statement(conn, "sb", b, "2028-02-01", "2028-03-30", "2028-02-01", "2028-03-30")
        seed(conn, "ka", "2028-02-10", 100, "debit", category="Shopping")
        seed(conn, "ka", "2028-02-29", 100, "debit", category="Shopping")
        seed(conn, "sb", "2028-03-05", 10, "debit", category="Shopping")
        seed(conn, "ka", "2028-03-31", 10, "debit", category="Shopping")
        s = summarize_insights(conn, "2028-03")
        assert s["period"]["covered_through"] == "2028-03-30" and s["period"]["partial"] is True
        assert s["comparison"]["end"] == "2028-02-29"
        assert s["verdict"]["prev"]["money_out"] == 200

    def test_the_ledger_must_reach_back_for_a_comparison_in_the_selected_accounts_view(self, conn):
        # Kotak has October; SBI started on 10 Nov. SBI alone has nothing to compare with.
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "ka", a, "2026-10-01", "2026-11-30", "2026-10-01", "2026-11-30")
        add_statement(conn, "sb", b, "2026-11-10", "2026-11-20", "2026-11-10", "2026-11-20")
        seed(conn, "ka", "2026-10-05", 100, "debit", category="Shopping")
        seed(conn, "ka", "2026-11-05", 100, "debit", category="Shopping")
        seed(conn, "sb", "2026-11-12", 10, "debit", category="Shopping")
        assert summarize_insights(conn, "2026-11", account_id=b)["comparison"] is None
        assert summarize_insights(conn, "2026-11", account_id=a)["comparison"] is not None

    def test_a_gap_month_in_one_account_is_not_a_month_of_zero_to_compare_with(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        conn.execute("DELETE FROM statements")
        add_statement(conn, "ka", a, "2026-09-01", "2026-11-30", "2026-09-01", "2026-11-30")
        add_statement(conn, "sb", b, "2026-09-01", "2026-09-30", "2026-09-01", "2026-09-30")
        add_statement(conn, "sc", b, "2026-11-01", "2026-11-30", "2026-11-01", "2026-11-30")
        seed(conn, "ka", "2026-10-05", 100, "debit", category="Shopping")
        seed(conn, "sb", "2026-09-05", 100, "debit", category="Shopping")
        seed(conn, "sc", "2026-11-05", 100, "debit", category="Shopping")
        sbi = summarize_insights(conn, "2026-11", account_id=b)
        assert sbi["comparison"] is None
        assert sbi["comparison_note"] == [{"account_label": "SBI ··XXXb", "reason": "has no statement for October"}]
        # Kotak's October is there, but SBI has none: the all-accounts view is not compared either.
        assert summarize_insights(conn, "2026-11")["comparison"] is None
        assert summarize_insights(conn, "2026-11", account_id=a)["comparison"] is not None


class TestTheFilterOnPayments:
    def test_list_summary_and_facets_follow_the_account(self, conn):
        a, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        ka = seed(conn, "st_a", "2026-10-05", 300, "debit", category="Shopping")
        sb = seed(conn, "st_b", "2026-10-06", 200, "credit", category="Income", subcategory="Salary")
        assert {t["id"] for t in list_transactions(conn, account_id=a)} == {ka}
        assert {t["id"] for t in list_transactions(conn, account_id=b)} == {sb}
        assert {t["id"] for t in list_transactions(conn)} == {ka, sb}
        assert summarize_transactions(conn, account_id=a) == {"count": 1, "money_out": 300.0, "money_in": 0}
        assert list_transaction_facets(conn, account_id=b)["categories"] == ["Income"]
        assert list_transaction_facets(conn)["categories"] == ["Income", "Shopping"]

    def test_the_account_filter_and_a_statement_filter_both_apply(self, conn):
        _, b = add_account(conn, "a"), add_account(conn, "b", "sbi")
        seed(conn, "st_a", "2026-10-05", 300, "debit", category="Shopping")
        assert list_transactions(conn, statement_id="st_a", account_id=b) == []


@pytest.fixture
def api():
    from finance_copilot.api.deps import get_db
    from finance_copilot.main import app

    conn = make_conn()
    app.dependency_overrides[get_db] = lambda: conn
    yield TestClient(app), conn
    app.dependency_overrides.pop(get_db, None)
    conn.close()


class TestTheRoutes:
    def test_insights_takes_an_account_and_names_it(self, api):
        client, conn = api
        a, _ = add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
        seed(conn, "st_a", "2026-10-05", 300, "debit", category="Shopping")
        seed(conn, "st_b", "2026-10-06", 200, "debit", category="Shopping")
        conn.commit()
        whole = client.get("/api/insights", params={"month": "2026-10"}).json()
        mine = client.get("/api/insights", params={"month": "2026-10", "account_id": a}).json()
        assert whole["verdict"]["money_out"] == 500 and whole["account"] is None
        assert mine["verdict"]["money_out"] == 300 and mine["account"] == {"id": a, "label": "Kotak savings"}

    def test_an_account_that_does_not_exist_is_a_404_not_an_empty_answer(self, api):
        client, conn = api
        add_account(conn, "a")
        seed(conn, "st_a", "2026-10-05", 300, "debit", category="Shopping")
        conn.commit()
        response = client.get("/api/insights", params={"account_id": "acc_nobody"})
        assert response.status_code == 404 and "account" in response.json()["detail"].lower()
        assert client.get("/api/transactions", params={"account_id": "acc_nobody"}).status_code == 404

    def test_transactions_list_summary_and_facets_take_the_account_and_rows_carry_its_label(self, api):
        client, conn = api
        a, b = add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
        ka = seed(conn, "st_a", "2026-10-05", 300, "debit", category="Shopping")
        seed(conn, "st_b", "2026-10-06", 200, "credit", category="Income", subcategory="Salary")
        conn.commit()
        rows = client.get("/api/transactions", params={"include": "annotation"}).json()
        assert {r["account_label"] for r in rows} == {"Kotak savings", "SBI savings"}
        assert {r["account_id"] for r in rows} == {a, b}
        mine = client.get("/api/transactions", params={"account_id": a}).json()
        assert [r["id"] for r in mine] == [ka] and mine[0]["account_label"] == "Kotak savings"
        assert client.get("/api/transactions/summary", params={"account_id": a}).json()["money_out"] == 300
        assert client.get("/api/transactions/facets", params={"account_id": b}).json()["categories"] == ["Income"]
        one = client.get(f"/api/transactions/{ka}").json()
        assert one["account_id"] == a and one["account_label"] == "Kotak savings"
