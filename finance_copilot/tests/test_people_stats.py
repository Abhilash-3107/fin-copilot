"""People match-count / net stats and person transaction filter (roadmap 3.8)."""
from __future__ import annotations

import json
import sqlite3

import pytest

from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.people import (
    create_person,
    matched_transaction_ids,
    people_transaction_stats,
)
from finance_copilot.db.queries.transactions import list_transactions


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','test','1','2026-01')"
    )
    return conn


def _txn(conn, txn_id, amount, dc, desc, key=None, vpa=None):
    upi_meta = json.dumps({"vpa": vpa}) if vpa else None
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key, upi_meta) VALUES (?, 's1', '2026-01-10', ?, ?, ?, ?, ?)",
        (txn_id, amount, dc, desc, key, upi_meta),
    )


@pytest.fixture
def conn():
    c = _make_conn()
    yield c
    c.close()


class TestPeopleStats:
    def test_name_token_matches_and_nets(self, conn):
        p = create_person(conn, "Karabi", upi="karabi")
        _txn(conn, "t1", 500.0, "debit", "UPI/KARABI BORA/pay", key="KARABI BORA")
        _txn(conn, "t2", 200.0, "credit", "UPI/KARABI BORA/refund", key="KARABI BORA")
        _txn(conn, "t3", 100.0, "debit", "UPI/SOMEONE ELSE/x", key="SOMEONE ELSE")
        conn.commit()

        stats = people_transaction_stats(conn)[p["id"]]
        assert stats["matched"] == 2
        assert stats["sent"] == 500.0
        assert stats["received"] == 200.0
        assert stats["net"] == -300.0

    def test_person_without_token_gets_zeroes(self, conn):
        p = create_person(conn, "NoToken")
        _txn(conn, "t1", 500.0, "debit", "UPI/ANYONE/x", key="ANYONE")
        conn.commit()
        assert people_transaction_stats(conn)[p["id"]] == {
            "matched": 0, "sent": 0.0, "received": 0.0, "net": 0.0,
        }

    def test_vpa_token_exact_match(self, conn):
        p = create_person(conn, "Rahul", upi="rahul@okhdfc")
        _txn(conn, "t1", 100.0, "debit", "UPI to rahul", vpa="rahul@okhdfc")
        _txn(conn, "t2", 100.0, "debit", "UPI to notrahul", vpa="notrahul@okhdfc")
        conn.commit()
        stats = people_transaction_stats(conn)[p["id"]]
        assert stats["matched"] == 1


class TestPersonFilter:
    def test_list_transactions_person_token(self, conn):
        create_person(conn, "Karabi", upi="karabi")
        _txn(conn, "t1", 500.0, "debit", "UPI/KARABI BORA/pay", key="KARABI BORA")
        _txn(conn, "t2", 100.0, "debit", "UPI/OTHER/x", key="OTHER")
        conn.commit()
        rows = list_transactions(conn, person_token="karabi")
        assert [r["id"] for r in rows] == ["t1"]

    def test_person_token_no_match_returns_empty(self, conn):
        _txn(conn, "t1", 500.0, "debit", "UPI/OTHER/x", key="OTHER")
        conn.commit()
        assert list_transactions(conn, person_token="ghost") == []

    def test_matched_transaction_ids_helper(self, conn):
        _txn(conn, "t1", 500.0, "debit", "UPI/KARABI BORA/pay", key="KARABI BORA")
        conn.commit()
        assert matched_transaction_ids(conn, "karabi") == ["t1"]
        assert matched_transaction_ids(conn, "") == []
