"""Group rollup + settle-up query tests (roadmap 3.7)."""
from __future__ import annotations

import sqlite3

import pytest

from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.groups import (
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

    def test_untyped_credit_is_a_full_repayment(self, conn):
        # The 2026-07-27 review's H1 case: user pays a 1,000 dinner shared with
        # Rahul (he owes 500), Rahul sends 500 back. Settled, not "owes 250".
        g = create_group(conn, "Dinner")
        _txn(conn, "t1", "2026-01-05", 1000.0, "debit")
        _txn(conn, "t2", "2026-01-10", 500.0, "credit")
        add_member(conn, g["id"], "t1", people=["Rahul"])
        add_member(conn, g["id"], "t2", people=["Rahul"])
        conn.commit()

        assert settle_up_for_group(conn, g["id"]) == []

    @pytest.mark.parametrize("txn_type", ["reimbursement", "refund", "transfer"])
    def test_typed_repayment_credit_offsets_in_full(self, conn, txn_type):
        g = create_group(conn, "G")
        _txn(conn, "t1", "2026-01-05", 600.0, "debit")
        _txn(conn, "t2", "2026-01-10", 300.0, "credit")
        add_member(conn, g["id"], "t1", people=["Alice"])
        add_member(conn, g["id"], "t2", people=["Alice"], txn_type=txn_type)
        conn.commit()

        assert settle_up_for_group(conn, g["id"]) == []

    @pytest.mark.parametrize("txn_type", ["split", "event"])
    def test_split_typed_credit_is_still_a_full_repayment(self, conn, txn_type):
        # How the real ledger tags friends paying back a trip share: credit +
        # split. It is their share coming back, not an inflow to divide.
        g = create_group(conn, "G")
        _txn(conn, "t1", "2026-01-05", 400.0, "debit")
        _txn(conn, "t2", "2026-01-10", 200.0, "credit")
        add_member(conn, g["id"], "t1", people=["Alice"], txn_type="split")
        add_member(conn, g["id"], "t2", people=["Alice"], txn_type=txn_type)
        conn.commit()

        assert settle_up_for_group(conn, g["id"]) == []

    @pytest.mark.parametrize("txn_type", [None, "split", "event"])
    def test_shared_debit_is_equal_split(self, conn, txn_type):
        g = create_group(conn, "G")
        _txn(conn, "t1", "2026-01-05", 300.0, "debit")
        add_member(conn, g["id"], "t1", people=["Alice", "Bob"], txn_type=txn_type)
        conn.commit()

        by_person = {s["person"]: s["net"] for s in settle_up_for_group(conn, g["id"])}
        assert by_person == {"Alice": 100.0, "Bob": 100.0}

    @pytest.mark.parametrize("txn_type", ["transfer", "refund"])
    def test_money_sent_is_owed_back_in_full(self, conn, txn_type):
        # User sends Alice 400 (a loan, or refunding her overpayment): all of it
        # is hers to return, split only across the people named.
        g = create_group(conn, "G")
        _txn(conn, "t1", "2026-01-05", 400.0, "debit")
        add_member(conn, g["id"], "t1", people=["Alice"], txn_type=txn_type)
        conn.commit()

        assert settle_up_for_group(conn, g["id"]) == [{"person": "Alice", "name": "Alice", "net": 400.0}]

    def test_reimbursement_debit_has_no_effect(self, conn):
        # Alice covered a cab that never hit the user's ledger; the user pays her
        # back 333. That settles an off-ledger debt, so nothing is owed either way.
        g = create_group(conn, "G")
        _txn(conn, "t1", "2026-01-06", 333.0, "debit")
        add_member(conn, g["id"], "t1", people=["Alice"], txn_type="reimbursement")
        conn.commit()

        assert settle_up_for_group(conn, g["id"]) == []

    def test_person_ids_resolve_to_names(self, conn):
        conn.execute("INSERT INTO people (id, name) VALUES ('p1', 'Rahul Mehta')")
        g = create_group(conn, "G")
        _txn(conn, "t1", "2026-01-05", 300.0, "debit")
        add_member(conn, g["id"], "t1", people=["p1", "Legacy Name"])
        conn.commit()

        settle = {s["person"]: s["name"] for s in settle_up_for_group(conn, g["id"])}
        # Unknown ids (free-text tags from before the people table) show as-is.
        assert settle == {"p1": "Rahul Mehta", "Legacy Name": "Legacy Name"}

    def test_repayment_from_two_people_is_split_between_them(self, conn):
        g = create_group(conn, "G")
        _txn(conn, "t1", "2026-01-05", 300.0, "debit")
        _txn(conn, "t2", "2026-01-10", 200.0, "credit")
        add_member(conn, g["id"], "t1", people=["Alice", "Bob"])  # each owes 100
        add_member(conn, g["id"], "t2", people=["Alice", "Bob"])  # each repays 100
        conn.commit()

        assert settle_up_for_group(conn, g["id"]) == []

    def test_rounding_settles_to_zero(self, conn):
        # 100 / 3 sharers leaves repeating decimals; paying back the rounded
        # share must still read as settled.
        g = create_group(conn, "G")
        _txn(conn, "t1", "2026-01-05", 100.0, "debit")
        _txn(conn, "t2", "2026-01-10", 33.33, "credit")
        _txn(conn, "t3", "2026-01-10", 33.33, "credit")
        add_member(conn, g["id"], "t1", people=["Alice", "Bob"])
        add_member(conn, g["id"], "t2", people=["Alice"])
        add_member(conn, g["id"], "t3", people=["Bob"])
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
