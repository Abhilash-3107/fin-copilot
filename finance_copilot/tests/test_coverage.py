"""Which days each account's statements cover (db/queries/coverage.py).

Only a printed period is exact. A statement that printed none covers, for an account's first
statement, from its first payment's date (nobody has seen the days before it), and for a
later one from the day after the account's previous statement ends (never later than its own
first payment, never before the 1st of its month). It runs on to the end of its last
payment's month when a later statement exists. A month whose coverage does not start on the
1st, in an account that has an earlier statement, has a hole at its start.
"""
from __future__ import annotations

import pytest
from test_insights_accounts import add_account, add_statement, make_conn, seed

from finance_copilot.db.queries.coverage import month_coverage, statement_spans
from finance_copilot.db.queries.insights import summarize_insights


@pytest.fixture
def conn():
    conn = make_conn()
    yield conn
    conn.close()


def unprinted(conn, statement_id, account, month, first_day, last_day):
    add_statement(conn, statement_id, account, f"{month}-{first_day:02d}", f"{month}-{last_day:02d}")
    seed(conn, statement_id, f"{month}-{first_day:02d}", 10, "debit", category="Shopping")
    seed(conn, statement_id, f"{month}-{last_day:02d}", 10, "debit", category="Shopping")


def spans_of(conn, account):
    return [(s["start"], s["end"]) for s in statement_spans(conn, account)[account]]


def test_an_accounts_first_unprinted_statement_covers_from_its_first_payment(conn):
    a = add_account(conn, "a")
    conn.execute("DELETE FROM statements")
    unprinted(conn, "oct", a, "2026-10", 15, 29)
    assert spans_of(conn, a) == [("2026-10-15", "2026-10-29")]


def test_a_later_unprinted_statement_chains_from_the_day_after_the_previous_one_ends(conn):
    a = add_account(conn, "a")
    conn.execute("DELETE FROM statements")
    unprinted(conn, "oct", a, "2026-10", 3, 29)
    unprinted(conn, "nov", a, "2026-11", 3, 27)
    assert spans_of(conn, a) == [("2026-10-03", "2026-10-31"), ("2026-11-01", "2026-11-27")]


def test_a_later_unprinted_statement_after_a_missing_month_still_shows_the_missing_month(conn):
    a = add_account(conn, "a")
    conn.execute("DELETE FROM statements")
    unprinted(conn, "sep", a, "2026-09", 3, 28)
    unprinted(conn, "nov", a, "2026-11", 3, 27)
    assert spans_of(conn, a) == [("2026-09-03", "2026-09-30"), ("2026-11-01", "2026-11-27")]


def test_a_first_statement_starting_mid_month_makes_the_next_month_not_comparable(conn):
    from finance_copilot.db.queries.books import books

    a = add_account(conn, "a", name="Kotak savings")
    conn.execute("DELETE FROM statements")
    unprinted(conn, "oct", a, "2026-10", 15, 28)
    unprinted(conn, "nov", a, "2026-11", 1, 12)
    october = summarize_insights(conn, "2026-10")
    assert october["period"]["partial"] is False  # the account only began that month: no hole
    november = summarize_insights(conn, "2026-11")
    assert november["comparison"] is None
    assert november["comparison_note"] == [{"account_label": "Kotak savings", "reason": "has no statement for 1-12 Oct"}]
    cell = books(conn)["accounts"][0]["cells"]["2026-10"]
    assert cell["full"] is False and cell["covered"] == [["2026-10-15", "2026-10-31"]]


def test_an_unprinted_statement_starts_the_day_after_the_previous_one_ends_when_that_is_later(conn):
    a = add_account(conn, "a")
    conn.execute("DELETE FROM statements")
    add_statement(conn, "first", a, "2026-10-02", "2026-10-15", "2026-10-01", "2026-10-15")
    unprinted(conn, "second", a, "2026-10", 20, 28)
    assert spans_of(conn, a) == [("2026-10-01", "2026-10-15"), ("2026-10-16", "2026-10-28")]


def test_an_unprinted_statement_never_starts_after_its_own_first_payment(conn):
    a = add_account(conn, "a")
    conn.execute("DELETE FROM statements")
    add_statement(conn, "first", a, "2026-10-02", "2026-10-25", "2026-10-01", "2026-10-25")
    unprinted(conn, "second", a, "2026-10", 20, 28)  # overlaps the first
    assert spans_of(conn, a)[1][0] == "2026-10-20"


def test_a_printed_period_is_exact_and_only_widened_to_hold_its_own_payments(conn):
    a = add_account(conn, "a")
    conn.execute("DELETE FROM statements")
    add_statement(conn, "p", a, "2026-10-12", "2026-10-20", "2026-10-10", "2026-10-25")
    seed(conn, "p", "2026-10-12", 10, "debit", category="Shopping")
    seed(conn, "p", "2026-10-28", 10, "debit", category="Shopping")  # outside what the header printed
    assert spans_of(conn, a) == [("2026-10-10", "2026-10-28")]


def test_monthly_files_whose_first_payment_is_on_the_3rd_chain_into_full_months(conn):
    a = add_account(conn, "a")
    conn.execute("DELETE FROM statements")
    for month, last in (("2026-09", 28), ("2026-10", 29), ("2026-11", 27)):
        unprinted(conn, month[5:], a, month, 3, last)
    october = summarize_insights(conn, "2026-10")
    assert october["period"]["partial"] is False and october["period"]["short"] == []
    # The account's first file begins on 3 Sep, so 1-2 Sep are not covered: October is complete
    # but cannot be compared with September. Every month after that can.
    assert october["comparison"] is None
    assert october["comparison_note"] == [{"account_label": "Kotak ··XXXa", "reason": "has no statement for 1-2 Sep"}]
    november = summarize_insights(conn, "2026-11")
    assert november["period"]["partial"] is True and november["comparison_note"] == []
    # The last statement stops where its payments stop: covered to the 27th, compared with 1 to 27 Oct.
    assert november["period"]["covered_through"] == "2026-11-27"
    assert november["comparison"]["end"] == "2026-10-27"


def test_a_month_that_starts_with_a_hole_has_one_when_the_account_has_an_earlier_statement(conn):
    a = add_account(conn, "a")
    conn.execute("DELETE FROM statements")
    add_statement(conn, "oct", a, "2026-10-01", "2026-10-31", "2026-10-01", "2026-10-31")
    add_statement(conn, "nov", a, "2026-11-10", "2026-11-30", "2026-11-10", "2026-11-30")
    items = statement_spans(conn, a)[a]
    got = month_coverage(items, "2026-11-01", "2026-11-30")
    assert got["hole_at_start"] is True and got["from"] == "2026-11-10"
    assert got["gap"] == ("2026-11-01", "2026-11-09")


def test_a_first_statement_that_starts_mid_month_has_no_hole(conn):
    a = add_account(conn, "a")
    conn.execute("DELETE FROM statements")
    add_statement(conn, "nov", a, "2026-11-10", "2026-11-30", "2026-11-10", "2026-11-30")
    got = month_coverage(statement_spans(conn, a)[a], "2026-11-01", "2026-11-30")
    assert got["hole_at_start"] is False and got["earlier"] is False and got["gap"] is None


def test_a_hole_inside_the_month_is_a_gap_between_two_ranges(conn):
    a = add_account(conn, "a")
    conn.execute("DELETE FROM statements")
    add_statement(conn, "k1", a, "2026-11-01", "2026-11-10", "2026-11-01", "2026-11-10")
    add_statement(conn, "k2", a, "2026-11-20", "2026-11-30", "2026-11-20", "2026-11-30")
    got = month_coverage(statement_spans(conn, a)[a], "2026-11-01", "2026-11-30")
    assert got["through"] == "2026-11-10" and got["gap"] == ("2026-11-11", "2026-11-19")


def test_no_statement_touching_the_month_is_none(conn):
    a = add_account(conn, "a")
    conn.execute("DELETE FROM statements")
    add_statement(conn, "oct", a, "2026-10-01", "2026-10-31", "2026-10-01", "2026-10-31")
    assert month_coverage(statement_spans(conn, a)[a], "2026-11-01", "2026-11-30") is None


def monthly_pair(conn, first_days=(3, 3)):
    """Kotak and SBI, three monthly files each with no printed period, paying from the given days."""
    a = add_account(conn, "a", name="Kotak savings")
    b = add_account(conn, "b", "sbi", name="SBI savings")
    conn.execute("DELETE FROM statements")
    for acct, tag, first_day in ((a, "k", first_days[0]), (b, "s", first_days[1])):
        for month, last in (("2026-09", 28), ("2026-10", 29), ("2026-11", 27)):
            unprinted(conn, f"{tag}{month[5:]}", acct, month, first_day, last)
    return a, b


def test_the_grid_reads_monthly_files_with_no_printed_period_as_full(conn):
    from finance_copilot.db.queries.books import books

    monthly_pair(conn)
    for account in books(conn)["accounts"]:
        # The first file begins on its first payment: nobody has seen 1-2 Sep.
        assert account["cells"]["2026-09"]["covered"] == [["2026-09-03", "2026-09-30"]], account["account_label"]
        assert account["cells"]["2026-09"]["full"] is False
        assert account["cells"]["2026-10"]["full"] is True
        # The newest file stops where its payments stop: nothing follows it.
        assert account["cells"]["2026-11"]["full"] is False
        assert account["cells"]["2026-11"]["covered"] == [["2026-11-01", "2026-11-27"]]


def test_two_accounts_that_start_paying_on_different_days_are_compared_over_the_same_days(conn):
    monthly_pair(conn, first_days=(1, 2))
    november = summarize_insights(conn, "2026-11")
    assert november["comparison"] == {"month": "2026-10", "start": "2026-10-01", "end": "2026-10-27", "uniform": True}
    assert november["comparison_note"] == []
    # Both accounts pay 10 on their first and last day of each file: October to the 27th holds the first only.
    assert november["verdict"]["money_out"] == 40 and november["verdict"]["prev"]["money_out"] == 20


def test_when_every_account_is_compared_the_tile_is_the_base_of_its_delta(conn):
    """The figure on a tile and the figure its delta is measured from are the same payments:
    this month over the accounts and days compared is the month itself."""
    a, b = monthly_pair(conn)
    for month in ("2026-11",):
        for acct in (None, a, b):
            s = summarize_insights(conn, month, account_id=acct)
            assert s["comparison"] is not None
            first = s["period"]["start"]
            clause, params = ("", ()) if acct is None else (" AND s.account_id = ?", (acct,))
            covered = conn.execute(
                "SELECT COALESCE(SUM(t.amount), 0) FROM transactions t JOIN statements s ON s.id = t.statement_id "
                f"WHERE t.debit_credit = 'debit' AND date(t.txn_date) BETWEEN ? AND ?{clause}",
                (first, s["period"]["covered_through"], *params),
            ).fetchone()[0]
            assert covered == s["verdict"]["money_out"], (month, acct)
