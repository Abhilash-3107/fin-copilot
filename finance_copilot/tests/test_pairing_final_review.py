"""Final review of R3 part C: the candidate queries seek by index, a payment waits on a
pair only when its label is none or Transfers, an Uncategorized list says why a row
waits, a move says when it released a hand-made pair, undo refuses a link inside one
account."""
from __future__ import annotations

import csv
import io
import re

import pytest
import test_pairing as _unit
import test_pairing_flows as _flows
from test_pairing import _in, _out
from test_pairing_fixes import _neft_pair

from finance_copilot.db.queries.common import unsorted_sql
from finance_copilot.db.queries.transfers import candidate_pairs_sql, has_open_candidate_sql

conn = _unit.conn
api = _flows.api


def _plan(conn, sql, params=()):
    return [tuple(r)[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]


def _scans(lines):
    """A full scan of a transactions alias (the queries alias it t, o, d or c)."""
    return [line for line in lines if re.match(r"SCAN (t|o|d|c)\b", line)]


def _seeks_by_cents(lines, alias):
    # SQLite 3.53 words an EXISTS probe "SEARCH o EXISTS USING INDEX ..."; older ones leave EXISTS out.
    return any(re.match(rf"SEARCH {alias}( EXISTS)? USING INDEX idx_txn_cents", line) for line in lines)


class TestTheCandidateQueriesSeekByIndex:
    def test_the_ids_path_scans_nothing(self, conn):
        sql, params = candidate_pairs_sql(ids=["a", "b"])
        lines = _plan(conn, sql, params)
        assert _scans(lines) == []
        assert _seeks_by_cents(lines, "c") and _seeks_by_cents(lines, "d")

    def test_the_between_path_scans_nothing(self, conn):
        sql, params = candidate_pairs_sql(between=("a", "b"))
        assert _scans(_plan(conn, sql, params)) == []

    def test_the_whole_ledger_path_scans_one_side_and_seeks_the_other(self, conn):
        sql, params = candidate_pairs_sql()
        lines = _plan(conn, sql, params)
        assert len(_scans(lines)) == 1
        assert _seeks_by_cents(lines, "c") or _seeks_by_cents(lines, "d")

    def test_waiting_on_a_pair_scans_only_the_row_asking(self, conn):
        lines = _plan(conn, f"SELECT t.id FROM transactions t LEFT JOIN annotations a ON a.transaction_id = t.id WHERE {unsorted_sql('a')}")
        assert len(_scans(lines)) == 1
        assert _seeks_by_cents(lines, "o")

    def test_has_open_candidate_seeks_the_other_side(self, conn):
        lines = _plan(conn, f"SELECT t.id FROM transactions t WHERE {has_open_candidate_sql('t')}")
        assert len(_scans(lines)) == 1
        assert _seeks_by_cents(lines, "o")

    def test_the_ids_path_still_finds_a_candidate_from_either_side(self, conn):
        from finance_copilot.db.queries.transfers import candidate_pairs

        _out(conn, "d")
        _in(conn, "c")
        assert candidate_pairs(conn, ids=["d"]) == [("d", "c", 0)]
        assert candidate_pairs(conn, ids=["c"]) == [("d", "c", 0)]
        assert candidate_pairs(conn, ids=["c", "d"]) == [("d", "c", 0)]
        assert candidate_pairs(conn, ids=["zz"]) == []


class TestOnlyATransfersGuessWaitsOnAPair:
    """A payment is held back for an own-account candidate only when its label is
    Transfers (or it has none, which is unsorted already)."""

    def _unsorted(self, conn):
        return {
            r["id"] for r in conn.execute(
                f"SELECT t.id FROM transactions t LEFT JOIN annotations a ON a.transaction_id = t.id WHERE {unsorted_sql('a')}"
            )
        }

    @pytest.mark.parametrize("label", [
        ("llm", "Food & Dining", "Restaurants"),
        ("rule", "Investments", "Mutual Funds"),
        ("rag_direct", "Shopping", None),
        ("learned_rule", "Income", "Salary"),
    ])
    def test_an_ordinary_confident_label_is_kept_next_to_a_same_amount_credit(self, conn, label):
        _out(conn, "d", amount=500.0, description="UPI/SWIGGY/1/UPI", ann=label)
        _in(conn, "c", amount=500.0, day=29)
        assert self._unsorted(conn) == {"c"}

    def test_a_transfers_guess_with_a_candidate_waits(self, conn):
        _out(conn, "d", description="NEFT/RAMESH", ann=("rule", "Transfers", "Peer Transfer"))
        _in(conn, "c")
        assert self._unsorted(conn) == {"d", "c"}

    def test_a_transfers_guess_without_a_candidate_stays_sorted(self, conn):
        _out(conn, "d", description="NEFT/RAMESH", ann=("rule", "Transfers", "Peer Transfer"))
        _in(conn, "c", day=10)
        assert self._unsorted(conn) == {"c"}

    @pytest.mark.parametrize("source", ["manual", "imported", "user_rule"])
    def test_the_users_own_transfers_label_is_never_held_back(self, conn, source):
        _out(conn, "d", description="NEFT/RAMESH", ann=(source, "Transfers", "Peer Transfer"))
        _in(conn, "c")
        assert self._unsorted(conn) == {"c"}

    def test_no_label_and_uncategorized_are_unsorted_with_or_without_a_candidate(self, conn):
        _out(conn, "d1", amount=100.0)
        _out(conn, "d2", amount=200.0, ann=("llm", "Uncategorized"))
        _in(conn, "c1", amount=100.0)
        assert self._unsorted(conn) == {"d1", "d2", "c1"}

    def test_a_self_transfers_label_is_not_held_back(self, conn):
        _out(conn, "d", description="NEFT/RAMESH", ann=("rule", "Self Transfers", "Self Transfer"))
        _in(conn, "c", ann=("rule", "Self Transfers", "Self Transfer"))
        assert self._unsorted(conn) == set()


class TestARowSaysItIsWaitingAndFacetsFollowTheEffectiveCategory:
    def _swiggy(self, conn):
        """An ordinary labelled payment next to the waiting pair: it keeps its label."""
        k = conn.execute("SELECT id FROM statements WHERE bank_name = 'kotak'").fetchone()[0]
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
            "VALUES ('sw', ?, '2026-10-09', 450.0, 'debit', 'UPI/SWIGGY/1/UPI')", (k,))
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) "
            "VALUES ('a_sw', 'sw', 'Food & Dining', 'Restaurants', 0.95, 'llm')")
        conn.commit()

    def test_the_list_flags_the_waiting_rows_and_keeps_their_guess(self, api):
        client, conn, k, s, debit, credit = _neft_pair(api)
        self._swiggy(conn)
        rows = {r["id"]: r for r in client.get("/api/transactions", params={"month": "2026-10", "include": "annotation"}).json()}
        assert rows[debit]["waiting_on_pair"] is True and rows[credit]["waiting_on_pair"] is True
        assert (rows[debit]["category"], rows[debit]["subcategory"]) == ("Transfers", "Peer Transfer")
        assert rows["sw"]["waiting_on_pair"] is False

    def test_the_detail_carries_the_flag(self, api):
        client, conn, k, s, debit, credit = _neft_pair(api)
        self._swiggy(conn)
        assert client.get(f"/api/transactions/{debit}").json()["waiting_on_pair"] is True
        assert client.get("/api/transactions/sw").json()["waiting_on_pair"] is False

    def test_the_flag_goes_when_the_user_answers(self, api):
        client, conn, k, s, debit, credit = _neft_pair(api)
        body = {"transaction_ids": [debit], "label": {"category": "Transfers", "subcategory": "Peer Transfer"}}
        assert client.post("/api/annotations/bulk-label", json=body).status_code == 200
        assert client.get(f"/api/transactions/{debit}").json()["waiting_on_pair"] is False

    def test_the_category_filter_offers_only_what_it_can_return(self, api):
        client, conn, k, s, debit, credit = _neft_pair(api)
        self._swiggy(conn)
        facets = client.get("/api/transactions/facets", params={"month": "2026-10"}).json()
        assert facets["categories"] == ["Food & Dining", "Uncategorized"]
        for category in facets["categories"]:
            assert client.get("/api/transactions/summary", params={"month": "2026-10", "category": category}).json()["count"] > 0

    def test_a_category_that_only_waiting_rows_have_is_not_offered_but_is_once_one_is_sorted(self, api):
        client, conn, k, s, debit, credit = _neft_pair(api)
        assert "Transfers" not in client.get("/api/transactions/facets").json()["categories"]
        client.post("/api/annotations/bulk-label", json={
            "transaction_ids": [debit], "label": {"category": "Transfers", "subcategory": "Peer Transfer"}})
        assert "Transfers" in client.get("/api/transactions/facets").json()["categories"]

    def test_the_export_carries_the_effective_category_and_no_extra_column(self, api):
        client, conn, k, s, debit, credit = _neft_pair(api)
        self._swiggy(conn)
        rows = {r["id"]: r for r in client.get("/api/transactions/export", params={"format": "json"}).json()}
        assert (rows[debit]["category"], rows[debit]["subcategory"]) == ("Uncategorized", None)
        assert rows["sw"]["category"] == "Food & Dining"
        assert "waiting_on_pair" not in rows[debit]
        text = client.get("/api/transactions/export", params={"format": "csv"}).text
        csv_rows = {r["id"]: r for r in csv.DictReader(io.StringIO(text))}
        assert csv_rows[debit]["category"] == "Uncategorized" and "waiting_on_pair" not in csv_rows[debit]


class TestUndoOfAnUnpairAfterAMove:
    def _unpaired_then_moved(self, api, *, move):
        from test_pairing_flows import _ingest, _Parser, _t

        client, conn = api
        b = _ingest(conn, _Parser("kotak", "XXXX9999", [_t(3, 5000.0, "debit", "IMPS/TO OWN SBI SAVINGS")]), "b")
        a = _ingest(conn, _Parser("sbi", "XXXX2222", [_t(4, 5000.0, "credit", "IMPS/FROM KOTAK")]), "a")
        debit = conn.execute("SELECT id FROM transactions WHERE statement_id = ?", (b.id,)).fetchone()[0]
        credit = conn.execute("SELECT id FROM transactions WHERE statement_id = ?", (a.id,)).fetchone()[0]
        token = client.post("/api/transfers/unpair", json={"transaction_id": debit}).json()["undo_token"]
        if move:
            assert client.patch(f"/api/statements/{b.id}", json={"account_id": a.account_id}).status_code == 200
        return client, conn, token, debit, credit

    def test_it_is_refused_when_the_two_payments_now_share_an_account(self, api):
        client, conn, token, debit, credit = self._unpaired_then_moved(api, move=True)
        resp = client.post("/api/annotations/review-queue/undo", json={"token": token})
        assert resp.status_code == 409
        assert "can't be undone" in resp.json()["detail"]
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 0
        assert client.get(f"/api/transactions/{debit}").json()["transfer"] is None

    def test_it_still_works_when_the_accounts_are_unchanged(self, api):
        client, conn, token, debit, credit = self._unpaired_then_moved(api, move=False)
        assert client.post("/api/annotations/review-queue/undo", json={"token": token}).status_code == 200
        assert client.get(f"/api/transactions/{debit}").json()["transfer"]["transaction_id"] == credit
