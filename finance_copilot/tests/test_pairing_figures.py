"""One "not sorted yet" everywhere (R3 part C): a payment that could be one side of a
transfer between the user's own accounts and that pairing was not sure about counts as
Uncategorized in every money figure, the same rows the Dashboard figure, the Money Map
row and the Transactions drill-through show, until the user answers it."""
from __future__ import annotations

import pytest
import test_pairing_flows as _flows
from test_pairing_flows import _ids, _ingest, _Parser, _t

from finance_copilot.db.queries.annotations import list_review_queue
from finance_copilot.db.queries.insights import summarize_insights
from finance_copilot.db.queries.transactions import list_transactions, summarize_transactions

api = _flows.api

MONTH = "2026-10"
THRESHOLD = 0.85


def _ledger(api, *, debit_label=("rule", "Transfers", "Peer Transfer"), credit_label=("rule", "Transfers", "Peer Transfer")):
    """The reviewer's unsigned NEFT pair over two accounts, with a salary and a meal so In and Out
    have something besides the pair."""
    client, conn = api
    k = _ingest(conn, _Parser("kotak", "XXXXXXXX1111", [
        _t(1, 100000.0, "credit", "NEFT/SALARY/ACME LTD"),
        _t(3, 450.0, "debit", "UPI/SWIGGY/412345678901/UPI"),
        _t(5, 25000.0, "debit", "NEFT/N123456789/ABHI B/SBIN0001234"),
    ]), "k")
    s = _ingest(conn, _Parser("sbi", "XXXXXXXX2222", [
        _t(5, 25000.0, "credit", "NEFT/N123456789/ABHI B/KKBK0000123"),
    ]), "s")
    debit, credit = _ids(conn, k.id, "debit", 25000.0), _ids(conn, s.id, "credit", 25000.0)
    for txn, label in ((debit, debit_label), (credit, credit_label)):
        if label:
            conn.execute(
                "INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) "
                "VALUES (?, ?, ?, ?, 0.97, ?)", (f"a_{txn}", txn, label[1], label[2], label[0]))
    for txn, cat, sub in ((_ids(conn, k.id, "credit", 100000.0), "Income", "Salary"), (_ids(conn, k.id, "debit", 450.0), "Food & Dining", "Restaurants")):
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) "
            "VALUES (?, ?, ?, ?, 0.97, 'rule')", (f"a_{txn}", txn, cat, sub))
    conn.commit()
    return client, conn, debit, credit


def _figures(conn):
    """Every figure that speaks of unsorted money, read the way each page reads it."""
    s = summarize_insights(conn, MONTH)
    row = next((c for c in s["categories"] if c["category"] == "Uncategorized"), None)
    drill = summarize_transactions(conn, month=MONTH, categories=["Uncategorized"])
    listed = list_transactions(conn, month=MONTH, categories=["Uncategorized"])
    v = s["verdict"]
    return {
        "dashboard": s["unexplained"]["total"],
        "money_map": row["gross"] if row else 0,
        "drill_out": drill["money_out"],
        "drill_ids_out": {t["id"] for t in listed if t["debit_credit"] == "debit"},
        "out": v["money_out"], "in": v["money_in"], "invested": v["invested"], "kept": v["kept"],
        "rows": round(sum(c["net"] for c in s["categories"]) + sum(e["total"] for e in s["breakdown"]["extras"]), 2),
    }


def _agree(f):
    assert f["dashboard"] == f["money_map"] == f["drill_out"]
    assert f["rows"] == f["out"]  # Out equals the sum of its breakdown
    assert round(f["in"] - f["out"] - f["invested"], 2) == f["kept"]


class TestTheUnsignedNeftPair:
    def test_every_figure_counts_the_debit_as_unsorted_until_it_is_paired(self, api):
        client, conn, debit, credit = _ledger(api)
        f = _figures(conn)
        _agree(f)
        assert f["dashboard"] == 25000.0 and f["drill_ids_out"] == {debit}
        assert f["out"] == 25450.0 and f["in"] == 125000.0
        assert f["kept"] == 99550.0

    def test_pairing_it_takes_25000_off_out_and_off_in_and_off_every_unsorted_figure(self, api):
        client, conn, debit, credit = _ledger(api)
        before = _figures(conn)
        assert client.post("/api/transfers/pair", json={"transaction_id": debit, "with_transaction_id": credit}).status_code == 200
        after = _figures(conn)
        _agree(after)
        assert after["dashboard"] == after["money_map"] == after["drill_out"] == 0
        assert after["out"] == before["out"] - 25000.0 and after["in"] == before["in"] - 25000.0
        assert after["kept"] == before["kept"]  # a move between accounts changes nothing kept

    def test_the_api_agrees_with_the_queries(self, api):
        client, conn, debit, credit = _ledger(api)
        month = client.get("/api/insights", params={"month": MONTH}).json()
        row = next(c for c in month["categories"] if c["category"] == "Uncategorized")
        assert month["unexplained"]["total"] == row["gross"] == 25000.0
        summary = client.get("/api/transactions/summary", params={"category": "Uncategorized", "month": MONTH}).json()
        assert summary["money_out"] == 25000.0

    def test_a_named_category_filter_does_not_also_list_the_unsorted_payment(self, api):
        client, conn, debit, credit = _ledger(api)
        listed = {t["id"] for t in list_transactions(conn, month=MONTH, categories=["Transfers"])}
        assert debit not in listed and credit not in listed

    def test_it_is_not_in_the_people_ledger_or_in_merchants_while_it_waits(self, api):
        client, conn, debit, credit = _ledger(api)
        s = summarize_insights(conn, MONTH)
        assert s["people"]["unmatched"]["count"] == 0 and s["people"]["items"] == []
        assert [e for e in s["breakdown"]["extras"] if e["key"] == "transfers"] == []

    def test_a_refund_linked_to_the_unsorted_debit_never_comes_off_uncategorized(self, api):
        client, conn, debit, credit = _ledger(api)
        k = conn.execute("SELECT id FROM statements WHERE bank_name = 'kotak'").fetchone()[0]
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
            "VALUES ('refund', ?, '2026-10-09', 1000.0, 'credit', 'REFUND')", (k,))
        conn.execute("INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) "
                     "VALUES ('a_refund', 'refund', 'Income', 'Refund', 1.0, 'manual')")
        a, b = sorted(["refund", debit])
        conn.execute("INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES ('l', ?, ?, 'refund')", (a, b))
        conn.commit()
        s = summarize_insights(conn, MONTH)
        row = next(c for c in s["categories"] if c["category"] == "Uncategorized")
        # Nothing says what the unsorted debit was, so no refund is taken off it: the row is the unsorted figure.
        assert (row["gross"], row["offsets"], row["net"]) == (25000.0, 0.0, 25000.0)
        assert row["net"] == s["unexplained"]["total"]
        assert not any(c["category"] == "Transfers" for c in s["categories"])
        assert round(sum(c["net"] for c in s["categories"]) + sum(e["total"] for e in s["breakdown"]["extras"]), 2) == s["verdict"]["money_out"]


class TestAnAmbiguousCandidateAlreadyLabelledSelfTransfers:
    @pytest.mark.parametrize("source", ["rule", "llm", "learned_rule", "policy"])
    def test_it_is_not_in_the_queue_and_no_figure_moves(self, api, source):
        client, conn, debit, credit = _ledger(api, debit_label=(source, "Self Transfers", "Self Transfer"), credit_label=(source, "Self Transfers", "Self Transfer"))
        # a second credit of the same amount in a third account makes the pair ambiguous
        h = _ingest(conn, _Parser("hdfc", "XXXXXXXX3333", [_t(6, 25000.0, "credit", "NEFT/OTHER/25000")]), "h")
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) "
            "VALUES ('a_h', ?, 'Self Transfers', 'Self Transfer', 0.97, ?)", (_ids(conn, h.id, "credit", 25000.0), source))
        conn.commit()
        queued = {i["transaction_id"] for i in list_review_queue(conn, THRESHOLD)}
        assert queued == set()
        f = _figures(conn)
        _agree(f)
        assert f["dashboard"] == 0
        assert (f["out"], f["in"]) == (450.0, 100000.0)

    def test_the_candidate_is_still_offered_when_the_user_opens_the_card(self, api):
        client, conn, debit, credit = _ledger(api, debit_label=("rule", "Self Transfers", "Self Transfer"), credit_label=("rule", "Self Transfers", "Self Transfer"))
        # not in the queue, but the pair stays one the user can make
        assert client.post("/api/transfers/pair", json={"transaction_id": debit, "with_transaction_id": credit}).status_code == 200


class TestAnOpenCandidateLabelledInvestments:
    def test_an_ordinary_label_is_kept_so_it_stays_invested_and_out_of_teach_me(self, api):
        """A payment of the same amount in another account within three days is a
        coincidence, not a question: only a Transfers guess waits on a pair."""
        client, conn, debit, credit = _ledger(
            api, debit_label=("rule", "Investments", "Mutual Fund SIP"), credit_label=None)
        assert debit not in {i["transaction_id"] for i in list_review_queue(conn, THRESHOLD)}
        f = _figures(conn)
        _agree(f)
        assert f["invested"] == 25000.0 and f["dashboard"] == 0 and f["out"] == 450.0
        assert f["kept"] == 125000.0 - 450.0 - 25000.0
