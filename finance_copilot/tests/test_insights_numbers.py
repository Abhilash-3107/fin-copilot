"""Numbers that agree: identities that must hold on any ledger, not just the demo.

Covers the cash-view identity, the like-for-like comparison window for a month
still in progress, the breakdown closing on Out, the single "not sorted"
definition, the recurring total, and the month-scoped people panel.
"""
from __future__ import annotations

import random
import sqlite3

import pytest
import ulid

from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.insights import summarize_insights
from finance_copilot.tests.test_insights import seed_txn


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month) "
        "VALUES ('s1','test','1','2026-01')"
    )
    yield conn
    conn.close()


def link(conn, a, b, link_type="refund"):
    x, y = sorted([a, b])
    conn.execute(
        "INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES (?, ?, ?, ?)",
        (str(ulid.ULID()), x, y, link_type),
    )


def group(conn, members, name="g"):
    gid = str(ulid.ULID())
    conn.execute("INSERT INTO transaction_groups (id, name) VALUES (?, ?)", (gid, name))
    for txn_id, txn_type in members:
        conn.execute(
            "INSERT INTO transaction_group_members (group_id, transaction_id, txn_type) "
            "VALUES (?, ?, ?)",
            (gid, txn_id, txn_type),
        )


def rich_ledger(conn):
    """Three months touching every mechanism: links, groups, refunds, unannotated
    rows, Transfers, ATM, Investments, Self Transfers, Income-labelled debits."""
    seed_txn(conn, "2026-01-01", 100000, "credit", category="Income", subcategory="Salary")
    seed_txn(conn, "2026-01-03", 20000, "credit", category="Income", subcategory="Opening Balance")
    seed_txn(conn, "2026-01-05", 1200.50, "debit", category="Food & Dining")
    seed_txn(conn, "2026-01-06", 310.25, "debit")  # unannotated
    seed_txn(conn, "2026-01-07", 5000, "debit", category="Transfers", subcategory="ATM Withdrawal")
    seed_txn(conn, "2026-01-08", 2000, "debit", category="Transfers", merchant="sanya")
    seed_txn(conn, "2026-01-09", 10000, "debit", category="Investments")
    seed_txn(conn, "2026-01-10", 7000, "debit", category="Self Transfers")
    seed_txn(conn, "2026-01-11", 700, "debit", category="Income")  # odd label on a debit
    seed_txn(conn, "2026-01-12", 450, "debit", category="Miscellaneous")
    shoes = seed_txn(conn, "2026-01-14", 3000, "debit", category="Shopping")
    refund = seed_txn(conn, "2026-02-02", 3000, "credit", category="Income", subcategory="Refund")
    link(conn, shoes, refund)
    concert = seed_txn(conn, "2026-01-20", 9999.99, "debit", category="Entertainment")
    s1 = seed_txn(conn, "2026-01-21", 3333.33, "credit", category="Transfers")
    s2 = seed_txn(conn, "2026-02-03", 3333.33, "credit", category="Transfers")
    group(conn, [(concert, "split"), (s1, "split"), (s2, "split")])
    seed_txn(conn, "2026-01-25", 800, "debit", category="Health", counterparty="MEDKART")
    seed_txn(conn, "2026-02-04", 800, "credit", category="Income", subcategory="Refund",
             counterparty="MEDKART")
    seed_txn(conn, "2026-02-01", 100000, "credit", category="Income", subcategory="Salary")
    seed_txn(conn, "2026-02-10", 1500, "debit", category="Food & Dining")
    seed_txn(conn, "2026-02-12", 2500, "debit", category="Transfers")
    seed_txn(conn, "2026-02-14", 12000, "debit", category="Investments")
    seed_txn(conn, "2026-02-27", 640, "debit")
    seed_txn(conn, "2026-02-28", 100, "credit", category="Entertainment")
    seed_txn(conn, "2026-03-01", 100000, "credit", category="Income", subcategory="Salary")
    seed_txn(conn, "2026-03-05", 2200, "debit", category="Food & Dining")
    seed_txn(conn, "2026-03-09", 90, "debit", category="Uncategorized")


CATEGORIES = [
    None, None, "Food & Dining", "Shopping", "Transfers", "Self Transfers", "Investments",
    "Income", "Uncategorized", "Miscellaneous", "Entertainment", "Health", "Travel",
]


def random_ledger(conn, seed, offsets=True):
    """80 random rows. With offsets=False there are no links, groups or Income >
    Refund credits, so nothing can be attributed to an unsorted debit."""
    rng = random.Random(seed)
    ids = {"debit": [], "credit": []}
    for _ in range(80):
        dc = rng.choice(["debit", "debit", "credit"])
        category = rng.choice(CATEGORIES)
        subcategory = None
        if category == "Income":
            subcategories = ["Salary", "Refund", "Opening Balance", None]
            if not offsets:
                subcategories.remove("Refund")
            subcategory = rng.choice(subcategories)
        elif category == "Transfers":
            subcategory = rng.choice(["ATM Withdrawal", None])
        day = rng.randint(0, 79)
        month, d = divmod(day, 28)
        txn_id = seed_txn(
            conn, f"2026-{month + 1:02d}-{d + 1:02d}", round(rng.uniform(1, 6000), 2), dc,
            category=category, subcategory=subcategory,
            counterparty=rng.choice([None, "ALPHA", "BETA", "GAMMA"]),
        )
        ids[dc].append(txn_id)
    if not offsets:
        return
    for _ in range(8):
        link(conn, rng.choice(ids["credit"]), rng.choice(ids["debit"]),
             rng.choice(["refund", "reimbursement", "split"]))
    for _ in range(3):
        members = [(i, "split") for i in rng.sample(ids["debit"], 2)]
        members += [(i, "split") for i in rng.sample(ids["credit"], 2)]
        group(conn, members)


def ledgers():
    yield "rich", rich_ledger
    for seed in range(25):
        yield f"random-{seed}", lambda c, s=seed: random_ledger(c, s)


LEDGER_PARAMS = [pytest.param(build, id=name) for name, build in ledgers()]


def cents(x):
    return round(x, 2)


@pytest.mark.parametrize("build", LEDGER_PARAMS)
class TestIdentities:
    def _all_months(self, conn, build):
        build(conn)
        months = [r[0] for r in conn.execute(
            "SELECT DISTINCT strftime('%Y-%m', txn_date) FROM transactions ORDER BY 1")]
        return [summarize_insights(conn, m) for m in months]

    def test_kept_is_in_minus_out_minus_invested(self, conn, build):
        for s in self._all_months(conn, build):
            v = s["verdict"]
            assert cents(v["money_in"] - v["money_out"] - v["invested"]) == v["kept"]
            if v["prev"]:
                p = v["prev"]
                assert cents(p["money_in"] - p["money_out"] - p["invested"]) == p["kept"]

    def test_breakdown_closes_on_out_to_the_paisa(self, conn, build):
        for s in self._all_months(conn, build):
            rows = sum(c["net"] for c in s["categories"])
            extras = sum(e["total"] for e in s["breakdown"]["extras"])
            assert s["breakdown"]["total"] == s["verdict"]["money_out"]
            assert cents(rows + extras) == s["verdict"]["money_out"], s["month"]

    def test_extras_are_named_and_only_when_nonzero(self, conn, build):
        for s in self._all_months(conn, build):
            keys = [e["key"] for e in s["breakdown"]["extras"]]
            assert len(keys) == len(set(keys))
            assert set(keys) <= {"transfers", "paid_back", "other"}
            assert all(e["total"] != 0 for e in s["breakdown"]["extras"])

    def test_category_net_is_gross_minus_offsets(self, conn, build):
        for s in self._all_months(conn, build):
            for c in s["categories"]:
                assert cents(c["gross"] - c["offsets"]) == c["net"]

    def test_unexplained_equals_uncategorized_gross(self, conn, build):
        for s in self._all_months(conn, build):
            row = next((c for c in s["categories"] if c["category"] == "Uncategorized"), None)
            if row is None:
                assert s["unexplained"] == {"count": 0, "total": 0}
            else:
                assert s["unexplained"] == {"count": row["count"], "total": row["gross"]}

    def test_recurring_header_equals_its_rows(self, conn, build):
        for s in self._all_months(conn, build):
            monthly = [r for r in s["recurring"] if r["active"] and r["cadence"] == "monthly"]
            assert s["recurring_summary"] == {
                "monthly_total": sum(r["amount"] for r in monthly),
                "count": len(monthly),
            }

    def test_comparison_is_null_together(self, conn, build):
        for s in self._all_months(conn, build):
            has = s["comparison"] is not None
            assert (s["verdict"]["prev"] is not None) is has
            assert all((c["delta"] is not None) is has for c in s["categories"])
            if not has:
                assert s["what_changed"] == []


@pytest.mark.parametrize("seed", range(25))
def test_unexplained_equals_uncategorized_net_when_nothing_offsets_it(conn, seed):
    # Credits labelled Uncategorized (or unannotated) are not known refunds, so
    # without a link, group or refund credit the unsorted row is exactly the
    # unexplained figure, net as well as gross.
    random_ledger(conn, seed, offsets=False)
    months = [r[0] for r in conn.execute(
        "SELECT DISTINCT strftime('%Y-%m', txn_date) FROM transactions ORDER BY 1")]
    for m in months:
        s = summarize_insights(conn, m)
        row = next((c for c in s["categories"] if c["category"] == "Uncategorized"), None)
        expected = row["net"] if row else 0
        assert s["unexplained"]["total"] == expected, m


class TestBreakdownRows:
    def test_extras_named_on_the_rich_ledger(self, conn):
        rich_ledger(conn)
        s = summarize_insights(conn, "2026-01")
        extras = {e["key"]: e for e in s["breakdown"]["extras"]}
        # ATM 5000 + person transfer 2000, nothing else labelled Transfers as a debit.
        assert extras["transfers"] == {
            "key": "transfers", "label": "Transfers and cash", "total": 7000, "count": 2,
        }
        assert extras["other"]["total"] == 700
        assert extras["other"]["label"] == "Other money out"
        # Offsets landing in January: the friend's share on the 21st only; the
        # refund and second share arrive in February.
        assert extras["paid_back"]["label"] == "Refunds and paybacks taken off the rows above"
        assert extras["paid_back"]["total"] == pytest.approx(3333.33)

    def test_no_extras_for_a_plain_month(self, conn):
        seed_txn(conn, "2026-01-05", 1000, "debit", category="Food & Dining")
        s = summarize_insights(conn, "2026-01")
        assert s["breakdown"] == {"total": 1000, "extras": []}

    def test_offset_without_debit_in_window_still_closes(self, conn):
        # A refund arrives in February for a January purchase.
        shoes = seed_txn(conn, "2026-01-14", 3000, "debit", category="Shopping")
        refund = seed_txn(conn, "2026-02-02", 3000, "credit", category="Income", subcategory="Refund")
        link(conn, shoes, refund)
        seed_txn(conn, "2026-02-20", 400, "debit", category="Food & Dining")
        s = summarize_insights(conn, "2026-02")
        assert s["verdict"]["money_out"] == 400
        shopping = next(c for c in s["categories"] if c["category"] == "Shopping")
        assert shopping["net"] == -3000
        assert s["breakdown"]["extras"] == [
            {"key": "paid_back", "label": "Refunds and paybacks taken off the rows above", "total": 3000, "count": None},
        ]


class TestUnsortedDefinition:
    def test_an_unsorted_credit_is_not_a_refund(self, conn):
        seed_txn(conn, "2026-01-05", 700, "debit", category="Uncategorized")
        seed_txn(conn, "2026-01-06", 500, "credit", category="Uncategorized")
        seed_txn(conn, "2026-01-07", 300, "credit")  # unannotated
        s = summarize_insights(conn, "2026-01")
        uncategorized = next(c for c in s["categories"] if c["category"] == "Uncategorized")
        assert (uncategorized["gross"], uncategorized["offsets"], uncategorized["net"]) == (700, 0, 700)
        assert s["unexplained"]["total"] == 700
        assert s["verdict"]["money_in"] == 800  # still money in, just not spend coming back

    def test_a_spend_labelled_credit_still_nets_its_category(self, conn):
        seed_txn(conn, "2026-01-05", 700, "debit", category="Shopping")
        seed_txn(conn, "2026-01-06", 200, "credit", category="Shopping")
        s = summarize_insights(conn, "2026-01")
        shopping = next(c for c in s["categories"] if c["category"] == "Shopping")
        assert shopping["net"] == 500

    def test_miscellaneous_is_a_real_label_not_unsorted(self, conn):
        seed_txn(conn, "2026-01-05", 450, "debit", category="Miscellaneous")
        s = summarize_insights(conn, "2026-01")
        assert s["unexplained"] == {"count": 0, "total": 0}

    def test_unannotated_and_uncategorized_are_one_bucket(self, conn):
        seed_txn(conn, "2026-01-05", 300, "debit")
        seed_txn(conn, "2026-01-06", 700, "debit", category="Uncategorized")
        seed_txn(conn, "2026-01-07", 50, "credit")  # credits are never unexplained spend
        s = summarize_insights(conn, "2026-01")
        assert s["unexplained"] == {"count": 2, "total": 1000}
        uncategorized = next(c for c in s["categories"] if c["category"] == "Uncategorized")
        assert (uncategorized["count"], uncategorized["gross"]) == (2, 1000)


class TestPeriodWindows:
    def test_complete_month_compares_to_the_whole_previous_month(self, conn):
        seed_txn(conn, "2026-01-01", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-01-31", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-02-02", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-02-28", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-03-01", 100, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-02")
        assert s["period"] == {
            "month": "2026-02", "start": "2026-02-01", "end": "2026-02-28",
            "covered_through": "2026-02-28", "partial": False,
        }
        assert s["comparison"] == {"month": "2026-01", "start": "2026-01-01", "end": "2026-01-31"}
        assert s["verdict"]["prev"]["money_out"] == 200

    def test_covered_to_the_31st_against_a_30_day_previous_month(self, conn):
        seed_txn(conn, "2026-04-01", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-04-30", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-05-01", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-05-31", 100, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-05")
        assert s["period"]["partial"] is False
        assert s["comparison"]["end"] == "2026-04-30"
        assert s["verdict"]["prev"]["money_out"] == 200

    def test_partial_month_compares_the_same_days(self, conn):
        for d in (1, 10, 15, 20, 28):
            seed_txn(conn, f"2026-10-{d:02d}", 100, "debit", category="Shopping")
        for d in (1, 14):
            seed_txn(conn, f"2026-11-{d:02d}", 10, "debit", category="Shopping")
        seed_txn(conn, "2026-11-15", 10, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-11")
        assert s["period"] == {
            "month": "2026-11", "start": "2026-11-01", "end": "2026-11-30",
            "covered_through": "2026-11-15", "partial": True,
        }
        assert s["comparison"] == {"month": "2026-10", "start": "2026-10-01", "end": "2026-10-15"}
        assert s["verdict"]["money_out"] == 30
        assert s["verdict"]["prev"]["money_out"] == 300  # 1st, 10th, 15th only
        shopping = next(c for c in s["categories"] if c["category"] == "Shopping")
        assert (shopping["prev_net"], shopping["delta"]) == (300, -270)

    def test_march_partial_against_february_28_days(self, conn):
        seed_txn(conn, "2026-02-01", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-02-28", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-03-01", 10, "debit", category="Shopping")
        seed_txn(conn, "2026-03-30", 10, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-03")
        # Covered to the 30th, February has only 28 days: the whole of February.
        assert s["period"]["partial"] is True
        assert s["comparison"]["end"] == "2026-02-28"
        assert s["verdict"]["prev"]["money_out"] == 200

    def test_march_partial_against_leap_february_29_days(self, conn):
        seed_txn(conn, "2028-02-01", 100, "debit", category="Shopping")
        seed_txn(conn, "2028-02-29", 100, "debit", category="Shopping")
        seed_txn(conn, "2028-03-01", 10, "debit", category="Shopping")
        seed_txn(conn, "2028-03-29", 10, "debit", category="Shopping")
        s = summarize_insights(conn, "2028-03")
        assert s["comparison"]["end"] == "2028-02-29"
        assert s["verdict"]["prev"]["money_out"] == 200

    def test_january_against_december_of_the_prior_year(self, conn):
        seed_txn(conn, "2025-12-01", 100, "debit", category="Shopping")
        seed_txn(conn, "2025-12-31", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-01-01", 10, "debit", category="Shopping")
        seed_txn(conn, "2026-01-12", 10, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-01")
        assert s["comparison"] == {"month": "2025-12", "start": "2025-12-01", "end": "2025-12-12"}
        assert s["verdict"]["prev"]["money_out"] == 100

    def test_first_month_of_the_ledger_has_no_comparison(self, conn):
        seed_txn(conn, "2026-01-05", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-01-20", 100, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-01")
        assert s["comparison"] is None
        assert s["verdict"]["prev"] is None
        assert s["what_changed"] == []
        assert all(c["delta"] is None and c["prev_net"] is None for c in s["categories"])

    def test_no_comparison_when_the_ledger_starts_inside_the_window(self, conn):
        # Data starts on 15 Oct: "1-15 Oct" cannot be compared with, so a half
        # November has nothing honest to be scored against.
        seed_txn(conn, "2026-10-15", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-10-28", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-11-01", 10, "debit", category="Shopping")
        seed_txn(conn, "2026-11-12", 10, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-11")
        assert s["period"]["partial"] is True
        assert s["comparison"] is None
        assert s["verdict"]["prev"] is None

    def test_a_gap_month_has_no_comparison(self, conn):
        # September and November have data, October has none: November must not
        # be scored against an empty October.
        seed_txn(conn, "2026-09-01", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-09-30", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-11-01", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-11-30", 100, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-11")
        assert s["comparison"] is None
        assert s["verdict"]["prev"] is None
        assert s["what_changed"] == []
        assert all(c["delta"] is None for c in s["categories"])

    def test_what_changed_never_names_uncategorized(self, conn):
        seed_txn(conn, "2026-09-01", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-10-05", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-10-06", 9000, "debit")  # unsorted jump
        seed_txn(conn, "2026-10-07", 4000, "debit", category="Travel")
        s = summarize_insights(conn, "2026-10")
        assert [c["category"] for c in s["what_changed"]] == ["Travel"]
        # The breakdown row still carries it, with its delta.
        assert any(c["category"] == "Uncategorized" for c in s["categories"])

    def test_a_month_with_data_on_both_sides_is_covered(self, conn):
        seed_txn(conn, "2026-08-10", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-09-10", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-10-10", 100, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-10")
        assert s["comparison"]["month"] == "2026-09"

    def test_hand_set_statement_period_end_extends_coverage(self, conn):
        # Statement periods normally equal the first and last transaction date,
        # but a period set past the last transaction is honoured as coverage.
        seed_txn(conn, "2026-10-01", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-10-31", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-11-01", 10, "debit", category="Shopping")
        seed_txn(conn, "2026-11-12", 10, "debit", category="Shopping")
        conn.execute(
            "UPDATE statements SET period_start='2026-10-01', period_end='2026-11-20' WHERE id='s1'"
        )
        s = summarize_insights(conn, "2026-11")
        assert s["period"]["covered_through"] == "2026-11-20"
        assert s["comparison"]["end"] == "2026-10-20"

    def test_statement_period_cap_at_month_end(self, conn):
        seed_txn(conn, "2026-10-01", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-11-01", 10, "debit", category="Shopping")
        conn.execute(
            "UPDATE statements SET period_start='2026-10-01', period_end='2026-12-31' WHERE id='s1'"
        )
        s = summarize_insights(conn, "2026-11")
        assert s["period"]["covered_through"] == "2026-11-30"
        assert s["period"]["partial"] is False

    def test_a_month_followed_by_later_data_is_complete(self, conn):
        # No statement periods and the last transaction of the month is on the
        # 28th: with a later month in the ledger, that month is not "in progress".
        seed_txn(conn, "2026-09-01", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-10-02", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-10-28", 100, "debit", category="Shopping")
        seed_txn(conn, "2026-11-02", 10, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-10")
        assert s["period"]["partial"] is False
        assert s["comparison"]["end"] == "2026-09-30"

    def test_offsets_are_windowed_by_credit_date(self, conn):
        # A refund arriving on the 20th does not net a comparison window that
        # ends on the 15th.
        seed_txn(conn, "2026-10-01", 5, "debit", category="Food & Dining")
        debit = seed_txn(conn, "2026-10-05", 1000, "debit", category="Shopping")
        refund = seed_txn(conn, "2026-10-20", 400, "credit", category="Income", subcategory="Refund")
        link(conn, debit, refund)
        seed_txn(conn, "2026-11-01", 10, "debit", category="Shopping")
        seed_txn(conn, "2026-11-15", 10, "debit", category="Shopping")
        s = summarize_insights(conn, "2026-11")
        assert s["comparison"]["end"] == "2026-10-15"
        shopping = next(c for c in s["categories"] if c["category"] == "Shopping")
        assert shopping["prev_net"] == 1000
        # Whole of October, seen from a complete November, nets the refund.
        seed_txn(conn, "2026-11-30", 10, "debit", category="Shopping")
        seed_txn(conn, "2026-12-01", 10, "debit", category="Shopping")
        full = summarize_insights(conn, "2026-11")
        assert next(c for c in full["categories"] if c["category"] == "Shopping")["prev_net"] == 600


class TestRecurringSummary:
    def _bill(self, conn, key, amount, days, category="Subscriptions"):
        for month, day in days:
            seed_txn(conn, f"2026-{month:02d}-{day:02d}", amount, "debit", category=category,
                     merchant=key.title(), counterparty=key)

    def test_header_counts_only_active_monthly_items(self, conn):
        self._bill(conn, "NETFLIX", 649, [(4, 5), (5, 5), (6, 5)])
        self._bill(conn, "SPOTIFY", 119, [(4, 9), (5, 9), (6, 9)])
        # Irregular: three months seen but the gaps are not monthly.
        self._bill(conn, "BESCOM", 2260, [(3, 2), (4, 28), (6, 20)], category="Utilities")
        # Stopped: last charge long before the newest transaction.
        self._bill(conn, "LINKEDIN", 499, [(1, 20), (2, 20)])
        seed_txn(conn, "2026-06-30", 100, "debit", category="Food & Dining")
        s = summarize_insights(conn, "2026-06")
        by_name = {r["name"]: r for r in s["recurring"]}
        assert by_name["Bescom"]["cadence"] == "irregular" and by_name["Bescom"]["active"]
        assert by_name["Linkedin"]["active"] is False
        # Only Netflix and Spotify are active and monthly.
        assert s["recurring_summary"] == {"monthly_total": 649 + 119, "count": 2}


class TestPeopleWindow:
    def _person(self, conn, name):
        conn.execute(
            "INSERT INTO people (id, name, upi, relationship) VALUES (?, ?, ?, 'friend')",
            (str(ulid.ULID()), name, name),
        )

    def test_only_transfers_in_the_month_count(self, conn):
        self._person(conn, "neha")
        self._person(conn, "rahul")
        seed_txn(conn, "2026-10-13", 440, "debit", category="Transfers", merchant="neha")
        seed_txn(conn, "2026-11-04", 3000, "debit", category="Transfers", merchant="rahul")
        seed_txn(conn, "2026-11-09", 1000, "credit", category="Transfers", merchant="rahul")
        s = summarize_insights(conn, "2026-11")
        assert [p["name"] for p in s["people"]["items"]] == ["rahul"]
        rahul = s["people"]["items"][0]
        assert (rahul["sent"], rahul["received"], rahul["count"]) == (3000, 1000, 2)
        octo = summarize_insights(conn, "2026-10")
        assert [p["name"] for p in octo["people"]["items"]] == ["neha"]

    def test_unmatched_is_windowed_too(self, conn):
        seed_txn(conn, "2026-10-13", 440, "debit", category="Transfers", merchant="stranger")
        seed_txn(conn, "2026-11-04", 3000, "debit", category="Transfers", merchant="other")
        s = summarize_insights(conn, "2026-11")
        assert s["people"]["unmatched"]["sent"] == 3000
