"""Insights aggregation tests: verdict math, offset netting, recurrence,
people ledger, merchant canonicalization, and the API route."""
from __future__ import annotations

import sqlite3

import pytest
import ulid
from fastapi.testclient import TestClient

from src.db.connection import init_db
from src.db.queries.insights import (
    _merge_merchant_keys,
    _prev_month,
    summarize_insights,
)
from src.db.queries.annotations import insert_annotation
from src.models.annotation import Annotation


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','test','1','2026-01')"
    )
    yield conn
    conn.close()


def seed_txn(conn, date, amount, dc, desc="X", category=None, subcategory=None,
             merchant=None, counterparty=None, balance=None):
    txn_id = str(ulid.ULID())
    conn.execute(
        """INSERT INTO transactions
           (id, statement_id, txn_date, amount, debit_credit, raw_description, running_balance, counterparty_key)
           VALUES (?, 's1', ?, ?, ?, ?, ?, ?)""",
        (txn_id, date, amount, dc, desc, balance, counterparty),
    )
    if category:
        insert_annotation(conn, Annotation(
            id=str(ulid.ULID()), transaction_id=txn_id, category=category,
            subcategory=subcategory, merchant=merchant, tags="", confidence=0.9,
            source="manual",
        ))
    return txn_id


class TestVerdict:
    def test_earned_excludes_refunds_and_opening_balance(self, conn):
        seed_txn(conn, "2026-01-31", 50000, "credit", category="Income", subcategory="Salary")
        seed_txn(conn, "2026-01-01", 20000, "credit", category="Income", subcategory="Opening Balance")
        seed_txn(conn, "2026-01-10", 500, "credit", category="Income", subcategory="Refund")
        s = summarize_insights(conn, "2026-01")
        assert s["verdict"]["earned"] == 50000

    def test_spend_excludes_transfers_investments_self(self, conn):
        seed_txn(conn, "2026-01-05", 1000, "debit", category="Food & Dining")
        seed_txn(conn, "2026-01-06", 30000, "debit", category="Investments")
        seed_txn(conn, "2026-01-07", 5000, "debit", category="Transfers")
        seed_txn(conn, "2026-01-08", 9000, "debit", category="Self Transfers")
        s = summarize_insights(conn, "2026-01")
        assert s["verdict"]["spent_gross"] == 1000
        assert s["verdict"]["invested"] == 30000

    def test_unannotated_debit_counts_as_spend(self, conn):
        seed_txn(conn, "2026-01-05", 750, "debit")
        s = summarize_insights(conn, "2026-01")
        assert s["verdict"]["spent_gross"] == 750
        assert s["unexplained"] == {"count": 1, "total": 750}

    def test_saved_and_rate(self, conn):
        seed_txn(conn, "2026-01-31", 40000, "credit", category="Income", subcategory="Salary")
        seed_txn(conn, "2026-01-05", 10000, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-01")
        assert s["verdict"]["saved"] == 30000
        assert s["verdict"]["savings_rate"] == 0.75


class TestOffsets:
    def test_group_split_credits_net_the_category(self, conn):
        # A 10k concert charge; two friends pay back 4k each in the same group.
        debit = seed_txn(conn, "2026-01-10", 10000, "debit", category="Entertainment")
        c1 = seed_txn(conn, "2026-01-10", 4000, "credit", category="Transfers")
        c2 = seed_txn(conn, "2026-01-11", 4000, "credit", category="Transfers")
        conn.execute("INSERT INTO transaction_groups (id, name) VALUES ('g1', 'concert')")
        for txn, ttype in ((debit, "split"), (c1, "split"), (c2, "split")):
            conn.execute(
                "INSERT INTO transaction_group_members (group_id, transaction_id, txn_type) VALUES ('g1', ?, ?)",
                (txn, ttype),
            )
        s = summarize_insights(conn, "2026-01")
        ent = next(c for c in s["categories"] if c["category"] == "Entertainment")
        assert ent["gross"] == 10000
        assert ent["net"] == 2000
        assert s["verdict"]["spent"] == 2000

    def test_linked_refund_nets_in_credit_month(self, conn):
        debit = seed_txn(conn, "2026-01-10", 3000, "debit", category="Shopping")
        credit = seed_txn(conn, "2026-02-05", 3000, "credit", category="Income", subcategory="Refund")
        a, b = sorted([debit, credit])
        conn.execute(
            "INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES ('l1', ?, ?, 'refund')",
            (a, b),
        )
        jan = summarize_insights(conn, "2026-01")
        feb = summarize_insights(conn, "2026-02")
        assert jan["verdict"]["spent"] == 3000
        shopping_feb = next(c for c in feb["categories"] if c["category"] == "Shopping")
        assert shopping_feb["net"] == -3000
        assert feb["verdict"]["spent"] == -3000

    def test_unlinked_refund_attributed_by_counterparty(self, conn):
        seed_txn(conn, "2026-01-10", 800, "debit", category="Health", counterparty="MEDKART")
        seed_txn(conn, "2026-01-18", 800, "credit", category="Income",
                 subcategory="Refund", counterparty="MEDKART")
        s = summarize_insights(conn, "2026-01")
        health = next(c for c in s["categories"] if c["category"] == "Health")
        assert health["net"] == 0
        assert s["verdict"]["earned"] == 0

    def test_unlinked_refund_without_counterparty_offsets_month(self, conn):
        seed_txn(conn, "2026-01-10", 2000, "debit", category="Shopping")
        seed_txn(conn, "2026-01-20", 500, "credit", category="Income", subcategory="Refund")
        s = summarize_insights(conn, "2026-01")
        shopping = next(c for c in s["categories"] if c["category"] == "Shopping")
        assert shopping["net"] == 2000  # no category attribution possible
        assert s["verdict"]["spent"] == 1500  # but the month total is corrected


class TestWhatChanged:
    def test_top_deltas_vs_prior_month(self, conn):
        seed_txn(conn, "2026-01-05", 1000, "debit", category="Food & Dining")
        seed_txn(conn, "2026-02-05", 6000, "debit", category="Food & Dining")
        seed_txn(conn, "2026-01-06", 4000, "debit", category="Travel")
        s = summarize_insights(conn, "2026-02")
        assert [d["category"] for d in s["what_changed"]] == ["Food & Dining", "Travel"]
        assert s["what_changed"][0]["delta"] == 5000
        assert s["what_changed"][1]["delta"] == -4000


class TestRecurring:
    def test_monthly_same_amount_detected(self, conn):
        for m in ("01", "02", "03"):
            seed_txn(conn, f"2026-{m}-02", 8000, "debit", category="Investments",
                     merchant="INDmoney", counterparty="INDMONEY")
        s = summarize_insights(conn, "2026-03")
        assert len(s["recurring"]) == 1
        item = s["recurring"][0]
        assert item["name"] == "INDmoney"
        assert item["cadence"] == "monthly"
        assert item["active"] is True

    def test_two_month_subscription_detected_but_not_food(self, conn):
        for m in ("02", "03"):
            seed_txn(conn, f"2026-{m}-15", 299, "debit", category="Subscriptions",
                     merchant="Hotstar", counterparty="HOTSTAR")
            seed_txn(conn, f"2026-{m}-16", 450, "debit", category="Food & Dining",
                     merchant="Zomato", counterparty="ZOMATO")
        s = summarize_insights(conn, "2026-03")
        assert [i["name"] for i in s["recurring"]] == ["Hotstar"]

    def test_high_frequency_fixed_price_excluded(self, conn):
        # A 50-rupee canteen coffee bought 8 times across 3 months is habit,
        # not commitment.
        for m, days in (("01", (3, 9, 20)), ("02", (4, 11, 25)), ("03", (2, 14))):
            for d in days:
                seed_txn(conn, f"2026-{m}-{d:02d}", 50, "debit", category="Food & Dining",
                         merchant="Canteen", counterparty="CANTEEN")
        s = summarize_insights(conn, "2026-03")
        assert s["recurring"] == []

    def test_lapsed_charge_reported_inactive(self, conn):
        for m in ("01", "02"):
            seed_txn(conn, f"2026-{m}-20", 499, "debit", category="Subscriptions",
                     merchant="LinkedIn", counterparty="LINKEDIN")
        seed_txn(conn, "2026-05-30", 100, "debit", category="Food & Dining")
        s = summarize_insights(conn, "2026-05")
        assert s["recurring"][0]["active"] is False


class TestPeople:
    def _seed_person(self, conn, name, upi, relationship="friend"):
        pid = str(ulid.ULID())
        conn.execute("INSERT INTO people (id, name, upi, relationship) VALUES (?, ?, ?, ?)",
                     (pid, name, upi, relationship))
        return pid

    def test_net_position_by_merchant_and_counterparty(self, conn):
        self._seed_person(conn, "sanya", "sanya")
        self._seed_person(conn, "ma", "karabi", "mom")
        seed_txn(conn, "2026-01-05", 5000, "debit", category="Transfers",
                 merchant="sanya", counterparty="SANYA PRASHANT")
        seed_txn(conn, "2026-01-08", 8000, "credit", category="Transfers",
                 counterparty="SANYA PRASHANT")
        seed_txn(conn, "2026-01-09", 2000, "credit", category="Transfers",
                 counterparty="KARABI BORA")
        s = summarize_insights(conn, "2026-01")
        by_name = {p["name"]: p for p in s["people"]["items"]}
        assert by_name["sanya"]["net"] == 3000
        assert by_name["ma"]["received"] == 2000

    def test_short_name_only_matches_exact_merchant(self, conn):
        self._seed_person(conn, "ma", None, "mom")
        # "ma" must not substring-match unrelated counterparties.
        seed_txn(conn, "2026-01-05", 700, "debit", category="Transfers",
                 counterparty="MAHESH KUMAR")
        s = summarize_insights(conn, "2026-01")
        assert s["people"]["items"] == []
        assert s["people"]["unmatched"]["sent"] == 700

    def test_settled_group_shares_excluded(self, conn):
        self._seed_person(conn, "sanya", "sanya")
        debit = seed_txn(conn, "2026-01-10", 10000, "debit", category="Entertainment")
        share = seed_txn(conn, "2026-01-10", 5000, "credit", category="Transfers",
                         merchant="sanya", counterparty="SANYA PRASHANT")
        conn.execute("INSERT INTO transaction_groups (id, name) VALUES ('g1', 'concert')")
        for txn, ttype in ((debit, "split"), (share, "split")):
            conn.execute(
                "INSERT INTO transaction_group_members (group_id, transaction_id, txn_type) VALUES ('g1', ?, ?)",
                (txn, ttype),
            )
        s = summarize_insights(conn, "2026-01")
        # The share already netted Entertainment; it is not money sanya gave.
        assert s["people"]["items"] == []


class TestMerchants:
    def test_prefix_merge(self):
        merged = _merge_merchant_keys(["ZOMATO", "ZOMATO LIMITED", "ZEPTO", "ZEPTO MARKETPLA", "UPI ABC"])
        assert merged["ZOMATO LIMITED"] == "ZOMATO"
        assert merged["ZEPTO MARKETPLA"] == "ZEPTO"
        assert merged["UPI ABC"] == "UPI ABC"

    def test_short_prefixes_never_merge(self):
        merged = _merge_merchant_keys(["UBER", "UBER EATS"])
        assert merged["UBER EATS"] == "UBER EATS"

    def test_month_merchants_with_count_and_avg(self, conn):
        for d, amt in ((5, 400), (12, 500), (20, 600)):
            seed_txn(conn, f"2026-01-{d:02d}", amt, "debit", category="Food & Dining",
                     merchant="Zomato", counterparty="ZOMATO")
        s = summarize_insights(conn, "2026-01")
        assert s["merchants"] == [{"name": "Zomato", "total": 1500, "count": 3, "avg": 500}]


class TestBalanceAndShape:
    def test_balance_series_last_point_per_day(self, conn):
        seed_txn(conn, "2026-01-05", 100, "debit", category="Shopping", balance=900)
        seed_txn(conn, "2026-01-05", 50, "debit", category="Shopping", balance=850)
        seed_txn(conn, "2026-01-06", 10, "debit", category="Shopping", balance=840)
        s = summarize_insights(conn, "2026-01")
        assert s["balance"] == [
            {"date": "2026-01-05", "balance": 850},
            {"date": "2026-01-06", "balance": 840},
        ]

    def test_empty_db(self, conn):
        assert summarize_insights(conn) == {"months": [], "month": None}

    def test_prev_month_rollover(self):
        assert _prev_month("2026-01") == "2025-12"
        assert _prev_month("2026-07") == "2026-06"

    def test_subcategory_alias_merge(self, conn):
        seed_txn(conn, "2026-01-05", 100, "debit", category="Food & Dining", subcategory="Dining")
        seed_txn(conn, "2026-01-06", 200, "debit", category="Food & Dining", subcategory="Restaurants")
        s = summarize_insights(conn, "2026-01")
        food = next(c for c in s["categories"] if c["category"] == "Food & Dining")
        assert food["subcategories"] == [{"name": "Restaurants", "total": 300, "count": 2}]


class TestRoute:
    def test_endpoint(self, conn):
        from src.main import app
        from src.api.deps import get_db as api_get_db

        seed_txn(conn, "2026-01-31", 1000, "credit", category="Income", subcategory="Salary")
        app.dependency_overrides[api_get_db] = lambda: conn
        try:
            client = TestClient(app)
            resp = client.get("/api/insights?month=2026-01")
            assert resp.status_code == 200
            assert resp.json()["verdict"]["earned"] == 1000
            assert client.get("/api/insights?month=bogus").status_code == 422
        finally:
            app.dependency_overrides.pop(api_get_db, None)
