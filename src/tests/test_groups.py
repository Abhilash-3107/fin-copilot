"""Group rollup + settle-up query tests (roadmap 3.7)."""
from __future__ import annotations

import sqlite3

import pytest

from src.db.connection import init_db
from src.db.queries.groups import (
    add_member,
    create_group,
    settle_up_for_group,
    summarize_groups,
    update_member,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','test','1','2026-01')"
    )
    return conn


def _txn(conn, txn_id, date, amount, dc, desc="X"):
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) VALUES (?, 's1', ?, ?, ?, ?)",
        (txn_id, date, amount, dc, desc),
    )


@pytest.fixture
def conn():
    c = _make_conn()
    yield c
    c.close()


class TestSummarize:
    def test_rollup_dates_totals_net(self, conn):
        g = create_group(conn, "Trip")
        _txn(conn, "t1", "2026-01-05", 300.0, "debit")
        _txn(conn, "t2", "2026-01-20", 100.0, "credit")
        add_member(conn, g["id"], "t1")
        add_member(conn, g["id"], "t2")
        conn.commit()

        s = summarize_groups(conn, [g["id"]])[g["id"]]
        assert s["member_count"] == 2
        assert s["first_date"] == "2026-01-05"
        assert s["last_date"] == "2026-01-20"
        assert s["spend"] == 300.0
        assert s["received"] == 100.0
        # net = received - spend
        assert s["net"] == -200.0

    def test_empty_group_ids(self, conn):
        assert summarize_groups(conn, []) == {}


class TestSettleUp:
    def test_equal_split_user_paid(self, conn):
        # User pays 300 for a dinner split with Alice and Bob → 3 sharers of 100 each.
        # Alice and Bob each owe the user 100.
        g = create_group(conn, "Dinner")
        _txn(conn, "t1", "2026-01-05", 300.0, "debit")
        add_member(conn, g["id"], "t1", people=["Alice", "Bob"])
        conn.commit()

        settle = settle_up_for_group(conn, g["id"])
        by_person = {s["person"]: s["net"] for s in settle}
        assert by_person == {"Alice": 100.0, "Bob": 100.0}

    def test_credit_reduces_what_person_owes(self, conn):
        # User pays 300 (Alice owes 150), then Alice pays 150 back (credit tagged to her).
        g = create_group(conn, "Split")
        _txn(conn, "t1", "2026-01-05", 300.0, "debit")
        _txn(conn, "t2", "2026-01-10", 300.0, "credit")
        add_member(conn, g["id"], "t1", people=["Alice"])  # share = 150 each → Alice owes 150
        add_member(conn, g["id"], "t2", people=["Alice"])  # credit share 150 → Alice net 0
        conn.commit()

        assert settle_up_for_group(conn, g["id"]) == []

    def test_members_without_people_ignored(self, conn):
        g = create_group(conn, "Solo")
        _txn(conn, "t1", "2026-01-05", 100.0, "debit")
        add_member(conn, g["id"], "t1")  # no people
        conn.commit()
        assert settle_up_for_group(conn, g["id"]) == []


class TestUpdateMemberRole:
    def test_role_is_persisted(self, conn):
        g = create_group(conn, "G")
        _txn(conn, "t1", "2026-01-05", 100.0, "debit")
        add_member(conn, g["id"], "t1")
        conn.commit()
        assert update_member(conn, g["id"], "t1", role="paid") is True
        row = conn.execute(
            "SELECT role FROM transaction_group_members WHERE group_id=? AND transaction_id=?",
            (g["id"], "t1"),
        ).fetchone()
        assert row["role"] == "paid"

    def test_empty_txn_type_clears_without_check_violation(self, conn):
        g = create_group(conn, "G")
        _txn(conn, "t1", "2026-01-05", 100.0, "debit")
        add_member(conn, g["id"], "t1", txn_type="split")
        conn.commit()
        # The UI sends "" when the user picks the "—" option; must clear, not violate CHECK.
        assert update_member(conn, g["id"], "t1", txn_type="") is True
        row = conn.execute(
            "SELECT txn_type FROM transaction_group_members WHERE group_id=? AND transaction_id=?",
            (g["id"], "t1"),
        ).fetchone()
        assert row["txn_type"] is None
