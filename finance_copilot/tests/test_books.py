"""The books grid (R3 part D): accounts by months, gaps visible, coverage and the balance
check in each cell, and the moves between the user's own accounts."""
from __future__ import annotations

import pytest
from test_insights_accounts import (
    add_account,
    add_statement,
    link,
    make_conn,
    seed,
)

from finance_copilot.db.queries.books import books


@pytest.fixture
def conn():
    conn = make_conn()
    yield conn
    conn.close()


@pytest.fixture
def api():
    from fastapi.testclient import TestClient

    from finance_copilot.api.deps import get_db
    from finance_copilot.main import app

    conn = make_conn()
    app.dependency_overrides[get_db] = lambda: conn
    yield TestClient(app), conn
    app.dependency_overrides.pop(get_db, None)
    conn.close()


def _statement(conn, statement_id, account, start, end, printed=True, checked=None, consistent=None, month=None):
    add_statement(conn, statement_id, account, start, end, start if printed else None, end if printed else None)
    if checked is not None:
        conn.execute(
            "UPDATE statements SET balance_checked = ?, balance_consistent = ? WHERE id = ?",
            (checked, consistent, statement_id),
        )


@pytest.fixture
def two(conn):
    """Kotak: Sep, Oct, Nov in full. SBI: Sep, then nothing in Oct, then 1 to 15 Nov."""
    a, b = add_account(conn, "a", name="Kotak savings"), add_account(conn, "b", "sbi", name="SBI savings")
    conn.execute("DELETE FROM statements")
    _statement(conn, "k9", a, "2026-09-01", "2026-09-30", checked=40, consistent=40)
    _statement(conn, "k10", a, "2026-10-01", "2026-10-31", checked=41, consistent=41)
    _statement(conn, "k11", a, "2026-11-01", "2026-11-30", checked=20, consistent=19)
    _statement(conn, "s9", b, "2026-09-01", "2026-09-30", checked=10, consistent=10)
    _statement(conn, "s11", b, "2026-11-01", "2026-11-15")
    for stmt, day in (("k9", "2026-09-05"), ("k10", "2026-10-05"), ("k10", "2026-10-28"), ("k11", "2026-11-05"),
                      ("s9", "2026-09-09"), ("s11", "2026-11-03")):
        seed(conn, stmt, day, 100, "debit", category="Shopping")
    return a, b


def _grid(conn):
    return books(conn)


def test_a_ledger_with_no_statements_has_no_grid(conn):
    assert books(conn) == {"months": [], "accounts": [], "transfers": []}


def test_one_row_per_account_one_column_per_month_oldest_first(conn, two):
    g = _grid(conn)
    assert g["months"] == ["2026-09", "2026-10", "2026-11"]
    assert [a["account_label"] for a in g["accounts"]] == ["Kotak savings", "SBI savings"]


def test_a_month_between_an_accounts_statements_is_a_gap_and_the_cell_says_so(conn, two):
    sbi = _grid(conn)["accounts"][1]
    assert sbi["cells"]["2026-10"]["gap"] is True
    assert sbi["cells"]["2026-09"]["gap"] is False and sbi["cells"]["2026-11"]["gap"] is False


def test_months_before_the_first_statement_and_after_the_last_are_not_cells(conn, two):
    a, b = two
    _statement(conn, "s12", b, "2026-12-01", "2026-12-10")
    seed(conn, "s12", "2026-12-02", 5, "debit", category="Shopping")
    g = _grid(conn)
    kotak = next(x for x in g["accounts"] if x["id"] == a)
    assert "2026-12" not in kotak["cells"]  # Kotak's last statement is November
    sbi = next(x for x in g["accounts"] if x["id"] == b)
    assert set(sbi["cells"]) == {"2026-09", "2026-10", "2026-11", "2026-12"}
    # An account that starts later has blanks before it.
    c = add_account(conn, "c", "hdfc", name="HDFC")
    _statement(conn, "h11", c, "2026-11-01", "2026-11-30")
    seed(conn, "h11", "2026-11-02", 5, "debit", category="Shopping")
    hdfc = next(x for x in _grid(conn)["accounts"] if x["id"] == c)
    assert set(hdfc["cells"]) == {"2026-11"}


def test_a_cell_counts_payments_and_covers_full_or_to_a_day(conn, two):
    g = _grid(conn)
    kotak, sbi = g["accounts"]
    assert kotak["cells"]["2026-10"]["payments"] == 2 and kotak["cells"]["2026-10"]["full"] is True
    nov = sbi["cells"]["2026-11"]
    assert nov["full"] is False and nov["covered"] == [["2026-11-01", "2026-11-15"]]


def test_a_statement_that_starts_mid_month_is_covered_from_that_day(conn):
    a = add_account(conn, "a", name="Kotak savings")
    conn.execute("DELETE FROM statements")
    _statement(conn, "k", a, "2026-10-10", "2026-10-31")
    seed(conn, "k", "2026-10-12", 5, "debit", category="Shopping")
    assert _grid(conn)["accounts"][0]["cells"]["2026-10"]["covered"] == [["2026-10-10", "2026-10-31"]]


def test_two_statements_covering_a_month_are_both_named_and_the_coverage_is_their_union(conn):
    a = add_account(conn, "a", name="Kotak savings")
    conn.execute("DELETE FROM statements")
    _statement(conn, "k1", a, "2026-10-01", "2026-10-20")
    _statement(conn, "k2", a, "2026-10-15", "2026-10-31")
    seed(conn, "k1", "2026-10-02", 5, "debit", category="Shopping")
    seed(conn, "k2", "2026-10-25", 5, "debit", category="Shopping")
    cell = _grid(conn)["accounts"][0]["cells"]["2026-10"]
    assert sorted(cell["statement_ids"]) == ["k1", "k2"] and cell["full"] is True


def test_the_balance_check_is_the_stored_one_added_up_over_the_cells_statements(conn, two):
    kotak, sbi = _grid(conn)["accounts"]
    assert kotak["cells"]["2026-10"]["balance_check"] == {"checked": 41, "consistent": 41}
    assert kotak["cells"]["2026-11"]["balance_check"] == {"checked": 20, "consistent": 19}


def test_a_statement_spanning_several_months_says_so_in_every_month_it_checks(conn):
    a = add_account(conn, "a", name="Kotak savings")
    conn.execute("DELETE FROM statements")
    _statement(conn, "k", a, "2026-08-01", "2026-10-31", checked=150, consistent=150)
    for day in ("2026-08-05", "2026-09-05", "2026-10-05"):
        seed(conn, "k", day, 10, "debit", category="Shopping")
    _statement(conn, "k11", a, "2026-11-01", "2026-11-30", checked=20, consistent=20)
    seed(conn, "k11", "2026-11-05", 10, "debit", category="Shopping")
    cells = _grid(conn)["accounts"][0]["cells"]
    # The check is of the whole file, so each month it covers carries the statement's own check and
    # the months it spans; a one-month statement names no months.
    for month in ("2026-08", "2026-09", "2026-10"):
        assert cells[month]["balance_check"] == {"checked": 150, "consistent": 150}
        assert cells[month]["statement_checks"] == [{"checked": 150, "consistent": 150, "months": ["2026-08", "2026-10"]}], month
    assert cells["2026-11"]["statement_checks"] == [{"checked": 20, "consistent": 20, "months": None}]


def test_a_cell_holding_several_statements_lists_each_ones_check(conn):
    a = add_account(conn, "a", name="Kotak savings")
    conn.execute("DELETE FROM statements")
    _statement(conn, "q", a, "2026-06-01", "2026-07-31", checked=150, consistent=148)
    _statement(conn, "j", a, "2026-07-01", "2026-07-31", checked=10, consistent=10)
    seed(conn, "q", "2026-06-05", 10, "debit", category="Shopping")
    seed(conn, "j", "2026-07-05", 10, "debit", category="Shopping")
    july = _grid(conn)["accounts"][0]["cells"]["2026-07"]
    assert sorted(july["statement_checks"], key=lambda c: c["checked"]) == [
        {"checked": 10, "consistent": 10, "months": None},
        {"checked": 150, "consistent": 148, "months": ["2026-06", "2026-07"]},
    ]
    assert july["balance_check"] == {"checked": 160, "consistent": 158}


def test_a_statement_without_balances_checks_nothing(conn, two):
    _kotak, sbi = _grid(conn)["accounts"]
    assert sbi["cells"]["2026-11"]["balance_check"] == {"checked": 0, "consistent": 0}


def test_only_the_last_twelve_months_that_have_a_statement_are_shown_and_gaps_between_them_stay(conn):
    a = add_account(conn, "a", name="Kotak savings")
    conn.execute("DELETE FROM statements")
    # Jan 2025 and then Jun 2025 to Dec 2026 minus nothing: 19 months in all.
    _statement(conn, "old", a, "2025-01-01", "2025-01-31")
    seed(conn, "old", "2025-01-05", 5, "debit", category="Shopping")
    for i in range(18):
        year, month = divmod(5 + i, 12)
        start = f"{2025 + year}-{month + 1:02d}-01"
        _statement(conn, f"m{i}", a, start, start[:8] + "28")
        seed(conn, f"m{i}", start[:8] + "05", 5, "debit", category="Shopping")
    g = _grid(conn)
    assert len(g["months"]) == 12 and g["months"][-1] == "2026-11"


def test_the_latest_balance_and_label_ride_with_the_account_and_never_the_full_number(conn, two):
    a, _ = two
    kotak = _grid(conn)["accounts"][0]
    assert kotak["account_ref_last4"] == "XXXa" and "account_ref" not in kotak


def test_a_paired_transfer_is_listed_once_from_the_debit_side_to_the_credit_side(conn, two):
    a, b = two
    out = seed(conn, "k10", "2026-10-28", 10000, "debit", category="Self Transfers", source="pair")
    arrived = seed(conn, "s9", "2026-10-28", 10000, "credit", category="Self Transfers", source="pair")
    link(conn, out, arrived, "transfer")
    transfers = _grid(conn)["transfers"]
    assert transfers == [{
        "transaction_id": out, "txn_date": "2026-10-28", "arrived": "2026-10-28", "amount": 10000.0,
        "from_label": "Kotak savings", "to_label": "SBI savings",
    }]


def test_a_refund_link_is_not_a_transfer(conn, two):
    out = seed(conn, "k10", "2026-10-28", 10000, "debit", category="Shopping")
    back = seed(conn, "s9", "2026-10-29", 10000, "credit", category="Income", subcategory="Refund")
    link(conn, out, back, "refund")
    assert _grid(conn)["transfers"] == []


def test_the_route_answers_the_same(api):
    client, route_conn = api
    # The route's connection is its own in-memory database: build the same ledger there.
    a = add_account(route_conn, "a", name="Kotak savings")
    route_conn.execute("DELETE FROM statements")
    _statement(route_conn, "k10", a, "2026-10-01", "2026-10-31")
    seed(route_conn, "k10", "2026-10-05", 5, "debit", category="Shopping")
    route_conn.commit()
    body = client.get("/api/accounts/books").json()
    assert body["months"] == ["2026-10"] and body["accounts"][0]["cells"]["2026-10"]["payments"] == 1
