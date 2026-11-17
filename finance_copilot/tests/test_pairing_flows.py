"""Pairing end to end on the API (R3 part C): upload, the row text, Unpair and its
undo, Teach Me candidates, deleting or starting over a paired statement, a hand
relabel of a paired payment, and the money math."""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import ulid
from fastapi.testclient import TestClient

from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.insights import _spend_offsets, summarize_insights
from finance_copilot.models.transaction import Transaction
from finance_copilot.parsers.base import StatementParser
from finance_copilot.pipeline.ingest import ingest_pdf
from finance_copilot.pipeline.pairing import pair_transfers


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


class _Parser(StatementParser):
    version = "1"

    def __init__(self, bank, ref, rows):
        self.bank_name, self._ref, self._rows = bank, ref, rows

    def detect(self, path, password=None):
        return True

    def parse(self, path, password=None):
        return [t.model_copy(update={"id": str(ulid.ULID())}) for t in self._rows]

    def extract_account_ref(self, path, password=None):
        return self._ref

    def extract_period(self, path, password=None):
        return None


def _t(day, amount, dc, description, balance=None):
    return Transaction(
        txn_date=date(2026, 10, day), amount=amount, debit_credit=dc, raw_description=description, running_balance=balance,
    )


def _ingest(conn, parser, tag):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"%PDF " + tag.encode())
        path = tmp.name
    try:
        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=parser):
            return ingest_pdf(path, conn=conn)
    finally:
        Path(path).unlink()


def _kotak(*extra):
    return _Parser("kotak", "XXXXXXXX1111", [
        _t(2, 40000.0, "credit", "NEFT/SALARY/ACME LTD"),
        _t(10, 450.0, "debit", "UPI/SWIGGY/412345678901/UPI"),
        _t(28, 5000.0, "debit", "IMPS/P2P/TRANSFER/TO OWN SBI SAVINGS"),
        *extra,
    ])


def _sbi(*extra):
    return _Parser("sbi", "XXXXXXXX2222", [
        _t(9, 412.0, "credit", "INT.PD/SAVINGS INTEREST"),
        _t(28, 5000.0, "credit", "IMPS/P2P/TRANSFER/FROM KOTAK", ),
        *extra,
    ])


@pytest.fixture
def api():
    from finance_copilot.api.deps import get_db
    from finance_copilot.main import app

    conn = _make_conn()
    app.dependency_overrides[get_db] = lambda: conn
    with patch("finance_copilot.api.routes.annotations._run_annotation_job"), \
            patch("finance_copilot.api.routes.annotations.embed_transaction", return_value=False), \
            patch("finance_copilot.api.routes.annotations.embed_transactions"), \
            patch("finance_copilot.api.routes.annotations._embed_in_background"):
        yield TestClient(app), conn
    app.dependency_overrides.pop(get_db, None)


def _ids(conn, statement_id, dc, amount):
    return conn.execute(
        "SELECT id FROM transactions WHERE statement_id = ? AND debit_credit = ? AND amount = ?",
        (statement_id, dc, amount),
    ).fetchone()[0]


@pytest.fixture
def paired(api):
    """A Kotak statement and an SBI statement whose 5,000 transfer paired by itself."""
    client, conn = api
    k = _ingest(conn, _kotak(), "k")
    s = _ingest(conn, _sbi(), "s")
    return client, conn, k, s, _ids(conn, k.id, "debit", 5000.0), _ids(conn, s.id, "credit", 5000.0)


def _state(conn):
    return {
        "annotations": [dict(r) for r in conn.execute("SELECT * FROM annotations ORDER BY transaction_id")],
        "links": [dict(r) for r in conn.execute("SELECT * FROM transaction_links ORDER BY id")],
        "unpaired": [tuple(r) for r in conn.execute("SELECT txn_a, txn_b FROM transfer_unpaired")],
    }


class TestUpload:
    def test_the_first_statement_pairs_nothing_the_second_pairs_the_transfer(self, api):
        client, conn = api
        k = _ingest(conn, _kotak(), "k")
        assert k.transfers_paired == 0
        s = _ingest(conn, _sbi(), "s")
        assert s.transfers_paired == 1
        assert conn.execute("SELECT COUNT(*) FROM transaction_links WHERE link_type = 'transfer'").fetchone()[0] == 1

    def test_the_upload_response_counts_the_transfers_paired(self, api):
        client, conn = api
        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=_kotak()):
            first = client.post("/api/statements/upload", files={"file": ("a.pdf", b"%PDF one", "application/pdf")})
        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=_sbi()):
            second = client.post("/api/statements/upload", files={"file": ("b.pdf", b"%PDF two", "application/pdf")})
        assert first.json()["transfers_paired"] == 0
        assert second.json()["transfers_paired"] == 1

    def test_pairing_runs_before_sorting_is_queued_so_the_queue_skips_paired_rows(self, api):
        client, conn = api
        _ingest(conn, _kotak(), "k")
        _ingest(conn, _sbi(), "s")
        # Neither paired payment is waiting for a sort: both are labelled by the time a job starts.
        unsorted = conn.execute(
            "SELECT t.raw_description FROM transactions t LEFT JOIN annotations a ON a.transaction_id = t.id "
            "WHERE a.id IS NULL AND t.amount = 5000"
        ).fetchall()
        assert unsorted == []

    def test_a_failure_in_pairing_does_not_fail_the_upload(self, api):
        client, conn = api
        _ingest(conn, _kotak(), "k")
        with patch("finance_copilot.pipeline.ingest.pair_transfers", side_effect=RuntimeError("boom")):
            s = _ingest(conn, _sbi(), "s")
        assert s.transfers_paired == 0
        assert conn.execute("SELECT COUNT(*) FROM transactions WHERE statement_id = ?", (s.id,)).fetchone()[0] == 2

    def test_a_statement_that_adds_a_second_account_pairs_older_rows_too(self, api):
        client, conn = api
        _ingest(conn, _kotak(), "k")
        s = _ingest(conn, _sbi(), "s")
        assert pair_transfers(conn) == []  # idempotent after the upload
        assert s.transfers_paired == 1


class TestTheRowSaysSo:
    def test_both_sides_say_where_the_money_went_and_came_from(self, paired):
        client, conn, k, s, debit, credit = paired
        out = client.get(f"/api/transactions/{debit}").json()
        inn = client.get(f"/api/transactions/{credit}").json()
        assert out["transfer"] == {
            "transaction_id": credit, "direction": "to", "account_label": "SBI ··2222",
            "txn_date": "2026-10-28", "text": "Moved to SBI ··2222",
        }
        assert inn["transfer"]["text"] == "Moved from Kotak ··1111"
        assert inn["transfer"]["transaction_id"] == debit
        assert (out["category"], out["source"]) == ("Self Transfers", "pair")

    def test_the_label_follows_a_name_the_user_gave_the_account(self, paired):
        client, conn, k, s, debit, credit = paired
        client.patch(f"/api/accounts/{s.account_id}", json={"name": "SBI savings"})
        assert client.get(f"/api/transactions/{debit}").json()["transfer"]["text"] == "Moved to SBI savings"

    def test_the_list_carries_it_and_an_unpaired_payment_carries_none(self, paired):
        client, conn, k, s, debit, credit = paired
        rows = {r["id"]: r for r in client.get("/api/transactions?include=annotation").json()}
        assert rows[debit]["transfer"]["direction"] == "to"
        assert rows[credit]["transfer"]["direction"] == "from"
        assert [r["transfer"] for r in rows.values() if r["id"] not in (debit, credit)] == [None] * (len(rows) - 2)

    def test_a_paired_payment_is_not_in_the_review_queue_or_counted_unsorted(self, paired):
        client, conn, k, s, debit, credit = paired
        queued = {i["transaction_id"] for i in client.get("/api/annotations/review-queue").json()}
        assert debit not in queued and credit not in queued


class TestUnpair:
    def test_unpair_removes_the_link_and_both_pair_labels_and_remembers_it(self, paired):
        client, conn, k, s, debit, credit = paired
        resp = client.post("/api/transfers/unpair", json={"transaction_id": credit})
        assert resp.status_code == 200
        assert sorted(resp.json()["unpaired"]) == sorted([debit, credit])
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM annotations WHERE source = 'pair'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM transfer_unpaired").fetchone()[0] == 1
        queued = {i["transaction_id"] for i in client.get("/api/annotations/review-queue").json()}
        assert {debit, credit} <= queued  # both go to To sort

    def test_pairing_never_puts_an_unpaired_pair_back(self, paired):
        client, conn, k, s, debit, credit = paired
        client.post("/api/transfers/unpair", json={"transaction_id": debit})
        assert pair_transfers(conn) == []
        s2 = _ingest(conn, _Parser("sbi", "XXXXXXXX2222", [_t(20, 77.0, "debit", "UPI/CHAI/412345678902/UPI")]), "s2")
        assert s2.transfers_paired == 0

    def test_a_side_the_user_sorted_themselves_keeps_their_label(self, api):
        client, conn = api
        k = _ingest(conn, _kotak(), "k")
        debit = _ids(conn, k.id, "debit", 5000.0)
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) "
            "VALUES ('m', ?, 'Self Transfers', 'Self Transfer', 1.0, 'manual')", (debit,))
        conn.commit()
        s = _ingest(conn, _sbi(), "s")
        credit = _ids(conn, s.id, "credit", 5000.0)
        client.post("/api/transfers/unpair", json={"transaction_id": credit})
        assert conn.execute("SELECT source FROM annotations WHERE transaction_id = ?", (debit,)).fetchone()[0] == "manual"
        assert conn.execute("SELECT COUNT(*) FROM annotations WHERE transaction_id = ?", (credit,)).fetchone()[0] == 0

    def test_undo_puts_the_pair_back_exactly(self, paired):
        client, conn, k, s, debit, credit = paired
        before = _state(conn)
        token = client.post("/api/transfers/unpair", json={"transaction_id": debit}).json()["undo_token"]
        assert _state(conn) != before
        assert client.post("/api/annotations/review-queue/undo", json={"token": token}).status_code == 200
        after = _state(conn)
        assert after == before  # link id, both annotations and no remembered unpairing
        # and a second undo is refused
        assert client.post("/api/annotations/review-queue/undo", json={"token": token}).status_code == 409

    def test_undo_is_refused_once_the_payments_changed(self, paired):
        client, conn, k, s, debit, credit = paired
        token = client.post("/api/transfers/unpair", json={"transaction_id": debit}).json()["undo_token"]
        client.post("/api/transfers/pair", json={"transaction_id": debit, "with_transaction_id": credit})
        assert client.post("/api/annotations/review-queue/undo", json={"token": token}).status_code == 409

    def test_unpairing_a_payment_that_is_not_paired_is_refused_and_unknown_is_404(self, paired):
        client, conn, k, s, debit, credit = paired
        other = _ids(conn, k.id, "debit", 450.0)
        assert client.post("/api/transfers/unpair", json={"transaction_id": other}).status_code == 409
        assert client.post("/api/transfers/unpair", json={"transaction_id": "nope"}).status_code == 404


class TestTeachMeCandidates:
    """Same amount, no sign of a self transfer: nothing is paired, the card offers it."""

    @pytest.fixture
    def unsigned(self, api):
        client, conn = api
        k = _ingest(conn, _Parser("kotak", "XXXXXXXX1111", [_t(20, 7000.0, "debit", "NEFT/RAMESH SHARMA")]), "k")
        s = _ingest(conn, _Parser("sbi", "XXXXXXXX2222", [_t(21, 7000.0, "credit", "NEFT/ANOTHER PERSON")]), "s")
        return client, conn, _ids(conn, k.id, "debit", 7000.0), _ids(conn, s.id, "credit", 7000.0), s

    def test_nothing_is_paired_and_the_candidate_leads_the_suggestions(self, unsigned):
        client, conn, debit, credit, s = unsigned
        assert s.transfers_paired == 0
        queue = {i["transaction_id"]: i for i in client.get("/api/annotations/review-queue").json()}
        first = queue[debit]["suggestions"][0]
        assert first["pair_with"] == credit
        assert (first["category"], first["subcategory"]) == ("Self Transfers", "Self Transfer")
        assert first["title"] == "Moved to SBI ··2222, 21 Oct"
        assert "1 day apart" in first["reason"]
        credit_first = queue[credit]["suggestions"][0]
        assert credit_first["pair_with"] == debit and credit_first["title"] == "Moved from Kotak ··1111, 20 Oct"
        assert len(queue[debit]["suggestions"]) <= 3

    def test_choosing_it_pairs_the_two_and_undo_takes_it_back(self, unsigned):
        client, conn, debit, credit, s = unsigned
        before = _state(conn)
        resp = client.post("/api/transfers/pair", json={"transaction_id": debit, "with_transaction_id": credit})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["transfer"]["text"] == "Moved to SBI ··2222"
        assert {a["source"] for a in _state(conn)["annotations"]} == {"pair"}
        queued = {i["transaction_id"] for i in client.get("/api/annotations/review-queue").json()}
        assert debit not in queued and credit not in queued
        assert client.post("/api/annotations/review-queue/undo", json={"token": body["undo_token"]}).status_code == 200
        assert _state(conn) == before

    def test_choosing_either_side_first_gives_the_same_pair(self, unsigned):
        client, conn, debit, credit, s = unsigned
        client.post("/api/transfers/pair", json={"transaction_id": credit, "with_transaction_id": debit})
        link = conn.execute("SELECT txn_a, txn_b FROM transaction_links").fetchone()
        assert sorted(tuple(link)) == sorted([debit, credit])

    def test_a_pair_the_user_took_apart_can_be_chosen_again(self, unsigned):
        client, conn, debit, credit, s = unsigned
        client.post("/api/transfers/pair", json={"transaction_id": debit, "with_transaction_id": credit})
        client.post("/api/transfers/unpair", json={"transaction_id": debit})
        queue = {i["transaction_id"]: i for i in client.get("/api/annotations/review-queue").json()}
        assert all("pair_with" not in s for s in queue[debit]["suggestions"])  # not offered again
        again = client.post("/api/transfers/pair", json={"transaction_id": debit, "with_transaction_id": credit})
        assert again.status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM transfer_unpaired").fetchone()[0] == 0

    def test_it_is_refused_when_the_payments_no_longer_fit(self, unsigned):
        client, conn, debit, credit, s = unsigned
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, confidence, source) "
            "VALUES ('m', ?, 'Gifts & Donations', 1.0, 'manual')", (credit,))
        conn.commit()
        resp = client.post("/api/transfers/pair", json={"transaction_id": debit, "with_transaction_id": credit})
        assert resp.status_code == 409
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 0

    @pytest.mark.parametrize("make", [
        lambda debit, credit: (debit, debit),                 # itself
        lambda debit, credit: (debit, "missing"),             # gone
    ])
    def test_a_pair_that_cannot_exist_is_refused(self, unsigned, make):
        client, conn, debit, credit, s = unsigned
        a, b = make(debit, credit)
        assert client.post("/api/transfers/pair", json={"transaction_id": a, "with_transaction_id": b}).status_code in (404, 409)

    def test_two_payments_of_the_same_direction_cannot_pair(self, api):
        client, conn = api
        k = _ingest(conn, _Parser("kotak", "XXXXXXXX1111", [_t(20, 7000.0, "debit", "NEFT/A")]), "k")
        s = _ingest(conn, _Parser("sbi", "XXXXXXXX2222", [_t(20, 7000.0, "debit", "NEFT/B")]), "s")
        resp = client.post("/api/transfers/pair", json={
            "transaction_id": _ids(conn, k.id, "debit", 7000.0), "with_transaction_id": _ids(conn, s.id, "debit", 7000.0)})
        assert resp.status_code == 409

    def test_an_ambiguous_payment_is_offered_every_candidate_up_to_two(self, api):
        client, conn = api
        _ingest(conn, _Parser("kotak", "XXXXXXXX1111", [_t(20, 900.0, "debit", "NEFT/A")]), "k")
        _ingest(conn, _Parser("sbi", "XXXXXXXX2222", [_t(20, 900.0, "credit", "NEFT/B")]), "s")
        _ingest(conn, _Parser("hdfc", "XXXXXXXX3333", [_t(22, 900.0, "credit", "NEFT/C")]), "h")
        queue = client.get("/api/annotations/review-queue").json()
        debit = next(i for i in queue if i["debit_credit"] == "debit")
        pairs = [s for s in debit["suggestions"] if "pair_with" in s]
        assert len(pairs) == 2
        assert all(s["reason"].startswith("One of 2 possible matches.") for s in pairs)
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 0


class TestDeletingOrStartingOver:
    def test_deleting_a_statement_frees_the_other_side(self, paired):
        client, conn, k, s, debit, credit = paired
        assert client.delete(f"/api/statements/{s.id}").status_code == 200
        # The Kotak payment is no longer Self Transfers with nobody on the other end.
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM annotations WHERE source = 'pair'").fetchone()[0] == 0
        queued = {i["transaction_id"] for i in client.get("/api/annotations/review-queue").json()}
        assert debit in queued

    def test_deleting_the_debit_side_statement_frees_the_credit(self, paired):
        client, conn, k, s, debit, credit = paired
        client.delete(f"/api/statements/{k.id}")
        assert conn.execute("SELECT COUNT(*) FROM annotations WHERE transaction_id = ?", (credit,)).fetchone()[0] == 0

    def test_no_pair_annotation_is_ever_left_without_its_link(self, paired):
        client, conn, k, s, debit, credit = paired
        client.delete(f"/api/statements/{s.id}")
        orphans = conn.execute(
            "SELECT COUNT(*) FROM annotations a WHERE a.source = 'pair' AND NOT EXISTS ("
            "SELECT 1 FROM transaction_links l WHERE l.link_type = 'transfer' AND a.transaction_id IN (l.txn_a, l.txn_b))"
        ).fetchone()[0]
        assert orphans == 0

    def test_deleting_forgets_the_users_unpairing_of_its_payments(self, paired):
        client, conn, k, s, debit, credit = paired
        client.post("/api/transfers/unpair", json={"transaction_id": debit})
        client.delete(f"/api/statements/{s.id}")
        assert conn.execute("SELECT COUNT(*) FROM transfer_unpaired").fetchone()[0] == 0

    def test_start_over_takes_the_pair_apart_and_pairs_a_signed_one_again(self, paired):
        client, conn, k, s, debit, credit = paired
        resp = client.delete(f"/api/statements/{s.id}/data")
        assert resp.status_code == 200
        # The line says "FROM KOTAK" and the Kotak side says "TO OWN SBI SAVINGS": a sign, so it pairs again.
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 1
        assert {r["source"] for r in conn.execute("SELECT source FROM annotations")} == {"pair"}

    def test_start_over_leaves_a_pair_chosen_by_hand_apart_and_the_partner_unsorted(self, api):
        client, conn = api
        k = _ingest(conn, _Parser("kotak", "XXXXXXXX1111", [_t(20, 7000.0, "debit", "NEFT/RAMESH SHARMA")]), "k")
        s = _ingest(conn, _Parser("sbi", "XXXXXXXX2222", [_t(21, 7000.0, "credit", "NEFT/ANOTHER")]), "s")
        debit, credit = _ids(conn, k.id, "debit", 7000.0), _ids(conn, s.id, "credit", 7000.0)
        client.post("/api/transfers/pair", json={"transaction_id": debit, "with_transaction_id": credit})
        client.delete(f"/api/statements/{s.id}/data")
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 0

    def test_start_over_keeps_what_the_user_took_apart_apart(self, paired):
        client, conn, k, s, debit, credit = paired
        client.post("/api/transfers/unpair", json={"transaction_id": debit})
        client.delete(f"/api/statements/{s.id}/data")
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 0

    def test_start_over_reports_the_annotations_it_cleared(self, paired):
        client, conn, k, s, debit, credit = paired
        assert client.delete(f"/api/statements/{s.id}/data").json()["annotations_deleted"] == 1


class TestMovingAStatement:
    def test_moving_a_statement_into_the_partners_account_dissolves_the_pair(self, paired):
        client, conn, k, s, debit, credit = paired
        assert client.patch(f"/api/statements/{s.id}", json={"account_id": k.account_id}).status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM annotations WHERE source = 'pair'").fetchone()[0] == 0
        # Not the user's decision about the pair, so nothing is remembered ...
        assert conn.execute("SELECT COUNT(*) FROM transfer_unpaired").fetchone()[0] == 0
        # ... and putting the statement in an account of its own makes them a transfer again.
        assert client.patch(f"/api/statements/{s.id}", json={"new_account": True}).status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM transaction_links WHERE link_type = 'transfer'").fetchone()[0] == 1


class TestARelabelByHand:
    def test_calling_a_paired_payment_something_else_takes_the_pair_apart(self, paired):
        client, conn, k, s, debit, credit = paired
        ann = conn.execute("SELECT id FROM annotations WHERE transaction_id = ?", (credit,)).fetchone()[0]
        resp = client.patch(f"/api/annotations/{ann}", json={"category": "Income", "subcategory": "Salary"})
        assert resp.status_code == 200
        assert conn.execute("SELECT source, category FROM annotations WHERE transaction_id = ?", (credit,)).fetchone()[:] == ("manual", "Income")
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 0
        # the other side is not left claiming a partner it no longer has
        assert conn.execute("SELECT COUNT(*) FROM annotations WHERE transaction_id = ?", (debit,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM transfer_unpaired").fetchone()[0] == 1

    def test_confirming_or_editing_inside_self_transfers_keeps_the_pair(self, paired):
        client, conn, k, s, debit, credit = paired
        ann = conn.execute("SELECT id FROM annotations WHERE transaction_id = ?", (credit,)).fetchone()[0]
        client.patch(f"/api/annotations/{ann}", json={"tags": ["savings"]})
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 1
        assert conn.execute("SELECT source FROM annotations WHERE id = ?", (ann,)).fetchone()[0] == "manual"


class TestTheMoneyMath:
    """A paired transfer is in no figure: not In, Out, Invested, Kept, a breakdown
    row, recurring, a merchant or a person. There is no account filter yet (R3 part
    D), so the whole ledger and each account's own payments are checked."""

    @staticmethod
    def _ledger(with_transfer: bool):
        conn = _make_conn()
        k = _ingest(conn, _Parser("kotak", "XXXXXXXX1111", [
            _t(2, 40000.0, "credit", "NEFT/SALARY/ACME LTD"),
            _t(10, 450.0, "debit", "UPI/SWIGGY/412345678901/UPI"),
            *([_t(28, 5000.0, "debit", "IMPS/P2P/TRANSFER/TO OWN SBI SAVINGS")] if with_transfer else []),
        ]), "k")
        s = _ingest(conn, _Parser("sbi", "XXXXXXXX2222", [
            _t(9, 412.0, "credit", "INT.PD/SAVINGS INTEREST"),
            *([_t(28, 5000.0, "credit", "IMPS/P2P/TRANSFER/FROM KOTAK")] if with_transfer else []),
        ]), "s")
        for stmt, rows in ((k, ("Income", "Food & Dining")), (s, ("Income",))):
            for txn_id, category in zip(
                [r[0] for r in conn.execute(
                    "SELECT id FROM transactions WHERE statement_id = ? AND amount != 5000 ORDER BY txn_date", (stmt.id,))],
                rows, strict=True,
            ):
                conn.execute(
                    "INSERT INTO annotations (id, transaction_id, category, confidence, source) VALUES (?, ?, ?, 1.0, 'manual')",
                    (str(ulid.ULID()), txn_id, category))
        conn.commit()
        return conn

    def test_a_paired_transfer_changes_no_figure_for_the_month(self):
        with_pair, without = self._ledger(True), self._ledger(False)
        assert with_pair.execute("SELECT COUNT(*) FROM transaction_links WHERE link_type = 'transfer'").fetchone()[0] == 1
        a, b = summarize_insights(with_pair, "2026-10"), summarize_insights(without, "2026-10")
        for section in ("verdict", "categories", "breakdown", "recurring", "recurring_summary", "people", "merchants", "unexplained"):
            assert a[section] == b[section], section
        assert a["verdict"]["money_in"] == 40412.0 and a["verdict"]["money_out"] == 450.0
        assert a["verdict"]["invested"] == 0
        assert a["breakdown"]["total"] == 450.0
        assert [c["category"] for c in a["categories"]] == ["Food & Dining"]

    def test_the_same_transfer_left_unpaired_is_counted_which_is_why_pairing_matters(self):
        conn = self._ledger(True)
        conn.execute("DELETE FROM transaction_links")
        conn.execute("DELETE FROM annotations WHERE source = 'pair'")
        conn.commit()
        verdict = summarize_insights(conn, "2026-10")["verdict"]
        assert verdict["money_in"] == 45412.0 and verdict["money_out"] == 5450.0

    def test_each_accounts_own_payments_leave_the_transfer_out_too(self):
        conn = self._ledger(True)
        for account_filter, expected_in, expected_out in (("kotak", 40000.0, 450.0), ("sbi", 412.0, 0.0)):
            rows = conn.execute(
                """SELECT t.debit_credit, t.amount FROM transactions t JOIN statements s ON s.id = t.statement_id
                   LEFT JOIN annotations a ON a.transaction_id = t.id
                   WHERE s.bank_name = ? AND COALESCE(a.category, '') != 'Self Transfers'""", (account_filter,)).fetchall()
            assert sum(r["amount"] for r in rows if r["debit_credit"] == "credit") == expected_in
            assert sum(r["amount"] for r in rows if r["debit_credit"] == "debit") == expected_out

    def test_a_transfer_link_nets_nothing_as_a_refund_would(self):
        conn = self._ledger(True)
        assert _spend_offsets(conn) == ({}, set())

    def test_a_refund_link_still_nets_while_a_transfer_link_beside_it_does_not(self):
        conn = _make_conn()
        k = _ingest(conn, _Parser("kotak", "XXXXXXXX1111", [
            _t(5, 800.0, "debit", "UPI/AMAZON/412345678901/UPI"),
            _t(9, 800.0, "credit", "UPI/AMAZON/REFUND"),
        ]), "k")
        bought, refund = _ids(conn, k.id, "debit", 800.0), _ids(conn, k.id, "credit", 800.0)
        conn.execute("INSERT INTO annotations (id, transaction_id, category, confidence, source) VALUES ('a', ?, 'Shopping', 1, 'manual')", (bought,))
        low, high = sorted((bought, refund))
        conn.execute("INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES ('r', ?, ?, 'refund')", (low, high))
        conn.commit()
        offsets, used = _spend_offsets(conn)
        assert sum(offsets.values()) == 800.0
        conn.execute("UPDATE transaction_links SET link_type = 'transfer'")
        conn.commit()
        assert _spend_offsets(conn) == ({}, set())


FIXTURES = Path(__file__).parent / "fixtures"


class TestTheFixtureStatements:
    """The two fictional statements the browser journey uploads, read by the real parser."""

    def test_the_signed_transfer_pairs_and_the_unsigned_one_waits_for_the_user(self, api):
        client, conn = api
        out = ingest_pdf(str(FIXTURES / "sample_statement_transfer_out.pdf"), conn=conn)
        assert out.transfers_paired == 0
        inn = ingest_pdf(str(FIXTURES / "sample_statement_transfer_in.pdf"), conn=conn)
        assert inn.transfers_paired == 1
        assert out.account_id != inn.account_id
        labels = {a["account_label"] for a in client.get("/api/accounts").json()}
        assert labels == {"ICICI ··7788", "Axis ··1234"}
        # The 12,000 pair: the debit names the other account's number, the credit lands a day later.
        debit = _ids(conn, out.id, "debit", 12000.0)
        credit = _ids(conn, inn.id, "credit", 12000.0)
        assert client.get(f"/api/transactions/{debit}").json()["transfer"]["text"] == "Moved to Axis ··1234"
        assert client.get(f"/api/transactions/{credit}").json()["transfer"]["text"] == "Moved from ICICI ··7788"
        # The 7,000 pair has no sign in either line: nothing is paired, Teach Me offers it.
        lone_out, lone_in = _ids(conn, out.id, "debit", 7000.0), _ids(conn, inn.id, "credit", 7000.0)
        assert client.get(f"/api/transactions/{lone_out}").json()["transfer"] is None
        queue = {i["transaction_id"]: i for i in client.get("/api/annotations/review-queue").json()}
        assert queue[lone_out]["suggestions"][0]["pair_with"] == lone_in
        assert queue[lone_in]["suggestions"][0]["pair_with"] == lone_out

    def test_the_existing_fixtures_pair_nothing_with_the_new_ones_or_each_other(self, api):
        client, conn = api
        for name in ("sample_statement", "sample_statement_nov", "sample_statement_other_bank"):
            assert ingest_pdf(str(FIXTURES / f"{name}.pdf"), conn=conn).transfers_paired == 0
