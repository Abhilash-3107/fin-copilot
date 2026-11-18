"""Fixes to R3 part C found in review: a hand relabel takes a pair apart whatever the
side's source, a payment with an open own-account candidate needs the user, undo
puts the partner back, user rules leave pairs alone, and the smaller edges."""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from finance_copilot.db.queries.annotations import list_review_queue, queue_counts
from finance_copilot.db.queries.review_undo import UndoCapture
from finance_copilot.pipeline.pairing import pair_transfers, transfer_candidates
from finance_copilot.tests import test_pairing as _unit
from finance_copilot.tests import test_pairing_flows as _flows
from finance_copilot.tests.test_pairing import HDFC, KOTAK, SBI, _annotation, _in, _links, _out
from finance_copilot.tests.test_pairing_flows import (
    _ids,
    _ingest,
    _kotak,
    _Parser,
    _sbi,
    _state,
    _t,
)

# The fixtures of the two modules these tests build on.
conn = _unit.conn
api = _flows.api
paired = _flows.paired

THRESHOLD = 0.85


def _manual_paired(api):
    """Kotak's 5,000 debit was sorted as Self Transfers by hand before SBI's statement
    arrived, so it paired and kept the user's label (the demo's Kotak side is this)."""
    client, conn = api
    k = _ingest(conn, _kotak(), "k")
    debit = _ids(conn, k.id, "debit", 5000.0)
    conn.execute(
        "INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) "
        "VALUES ('m', ?, 'Self Transfers', 'Self Transfer', 1.0, 'manual')", (debit,))
    conn.commit()
    s = _ingest(conn, _sbi(), "s")
    credit = _ids(conn, s.id, "credit", 5000.0)
    assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 1
    assert conn.execute("SELECT source FROM annotations WHERE transaction_id = ?", (debit,)).fetchone()[0] == "manual"
    return client, conn, debit, credit


def _taken_apart(conn, debit, credit):
    assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM annotations WHERE source = 'pair'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM transfer_unpaired").fetchone()[0] == 1


# 1. a relabel of a hand-labelled side -------------------------------------------------------------


class TestARelabelTakesAPairApartWhateverTheSource:
    def test_patching_the_manual_side(self, api):
        client, conn, debit, credit = _manual_paired(api)
        ann = conn.execute("SELECT id FROM annotations WHERE transaction_id = ?", (debit,)).fetchone()[0]
        assert client.patch(f"/api/annotations/{ann}", json={"category": "Food & Dining", "subcategory": None}).status_code == 200
        _taken_apart(conn, debit, credit)
        assert client.get(f"/api/transactions/{debit}").json()["transfer"] is None
        assert conn.execute("SELECT COUNT(*) FROM annotations WHERE transaction_id = ?", (credit,)).fetchone()[0] == 0

    def test_a_teach_me_answer_to_the_manual_side(self, api):
        client, conn, debit, credit = _manual_paired(api)
        body = {"transaction_ids": [debit], "label": {"category": "Food & Dining", "subcategory": None}}
        assert client.post("/api/annotations/bulk-label", json=body).status_code == 200
        _taken_apart(conn, debit, credit)

    def test_bulk_category_on_either_side(self, api):
        client, conn, debit, credit = _manual_paired(api)
        resp = client.post("/api/annotations/bulk-category", json={"transaction_ids": [credit], "category": "Income", "subcategory": "Salary"})
        assert resp.status_code == 200
        _taken_apart(conn, debit, credit)
        assert _annotation(conn, credit)["category"] == "Income"

    def test_the_demos_manual_kotak_side(self, api):
        from finance_copilot import demo_seed

        client, conn = api
        demo_seed.seed(conn, date(2026, 11, 15))
        row = conn.execute(
            "SELECT a.id, a.transaction_id FROM annotations a JOIN transaction_links l ON l.link_type = 'transfer' "
            "AND (l.txn_a = a.transaction_id OR l.txn_b = a.transaction_id) WHERE a.source = 'manual' LIMIT 1").fetchone()
        assert client.patch(f"/api/annotations/{row['id']}", json={"category": "Food & Dining", "subcategory": None}).status_code == 200
        assert client.get(f"/api/transactions/{row['transaction_id']}").json()["transfer"] is None
        assert conn.execute(
            "SELECT COUNT(*) FROM transaction_links WHERE txn_a = ? OR txn_b = ?", (row["transaction_id"],) * 2).fetchone()[0] == 0

    def test_a_rule_applied_to_existing_payments_leaves_a_pair_alone(self, paired):
        client, conn, k, s, debit, credit = paired
        rule = client.post("/api/rules", json={"pattern": "IMPS/P2P", "category": "Transfers", "subcategory": "Peer Transfer"}).json()
        before = _state(conn)
        assert client.post(f"/api/rules/{rule['id']}/apply").json()["protected"] == 2
        assert _state(conn) == before

    def test_confirming_or_re_saving_self_transfers_keeps_the_pair(self, api):
        client, conn, debit, credit = _manual_paired(api)
        ann = conn.execute("SELECT id FROM annotations WHERE transaction_id = ?", (debit,)).fetchone()[0]
        client.patch(f"/api/annotations/{ann}", json={"category": "Self Transfers", "subcategory": "Self Transfer"})
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 1


# 3. undo of an answer that unpaired its partner ----------------------------------------------------


class TestUndoPutsThePartnerBack:
    def test_undoing_a_bulk_label_of_the_manual_side_restores_the_pair_exactly(self, api):
        client, conn, debit, credit = _manual_paired(api)
        before = _state(conn)
        body = {"transaction_ids": [debit], "label": {"category": "Food & Dining", "subcategory": None}}
        token = client.post("/api/annotations/bulk-label", json=body).json()["undo_token"]
        assert _state(conn) != before
        assert client.post("/api/annotations/review-queue/undo", json={"token": token}).status_code == 200
        assert _state(conn) == before

    def test_undoing_a_bulk_label_of_a_pair_sourced_side_restores_both(self, paired):
        client, conn, k, s, debit, credit = paired
        before = _state(conn)
        body = {"transaction_ids": [debit], "label": {"category": "Food & Dining", "subcategory": None}}
        token = client.post("/api/annotations/bulk-label", json=body).json()["undo_token"]
        assert client.post("/api/annotations/review-queue/undo", json={"token": token}).status_code == 200
        assert _state(conn) == before

    def test_the_capture_snapshots_the_partners_of_the_answered_payments(self, paired):
        client, conn, k, s, debit, credit = paired
        capture = UndoCapture(conn, [debit])
        assert set(capture._before) == {debit, credit}
        capture.include(conn, [debit])
        other = _ids(conn, k.id, "debit", 450.0)
        capture.include(conn, [other])
        assert set(capture._before) == {debit, credit, other}


# 4. user rules never relabel a paired payment ------------------------------------------------------


class TestUserRulesLeaveAPairAlone:
    def test_the_preview_lists_paired_payments_as_protected(self, paired):
        client, conn, *_ = paired
        preview = client.post("/api/rules/preview", json={"pattern": "IMPS/P2P", "category": "Transfers", "subcategory": "Peer Transfer"}).json()
        assert (preview["matches"], preview["protected"], preview["relabel"]) == (2, 2, 0)

    def test_creating_a_rule_with_apply_to_existing_leaves_the_pair(self, paired):
        client, conn, *_ = paired
        before = _state(conn)
        resp = client.post("/api/rules", json={
            "pattern": "IMPS/P2P", "category": "Transfers", "subcategory": "Peer Transfer", "apply_to_existing": True})
        assert resp.status_code == 201
        assert _state(conn) == before

    def test_a_row_is_read_again_inside_the_loop(self, paired, monkeypatch):
        """A relabel earlier in the loop can unpair a later row (its partner); the loop must
        judge each row by what is stored when it gets there, not by the list it started with."""
        from finance_copilot.db.queries import user_rules

        client, conn, k, s, debit, credit = paired
        real = user_rules._matching_rows

        def stale(connection, pattern):
            rows = real(connection, pattern)
            for r in rows:
                r["source"] = "llm"  # as if listed before the pair was made
            return rows

        monkeypatch.setattr(user_rules, "_matching_rows", stale)
        rule = {"pattern": "IMPS/P2P", "category": "Transfers", "subcategory": "Peer Transfer",
                "category_id": "cat_transfers", "subcategory_id": "cat_tx_peer"}
        result = user_rules.apply_rule_to_existing(conn, rule)
        assert (result["relabeled"], result["protected"]) == (0, 2)


# 2. a payment with an open own-account candidate needs the user ----------------------------------


def _neft_pair(api, label_source="rule", confidence=0.95):
    """The reviewer's pair: Kotak debit and SBI credit, same day, no sign in either line, each
    already carrying a confident machine label."""
    client, conn = api
    k = _ingest(conn, _Parser("kotak", "XXXXXXXX1111", [_t(5, 25000.0, "debit", "NEFT/N123456789/ABHI B/SBIN0001234")]), "k")
    s = _ingest(conn, _Parser("sbi", "XXXXXXXX2222", [_t(5, 25000.0, "credit", "NEFT/N123456789/ABHI B/KKBK0000123")]), "s")
    debit, credit = _ids(conn, k.id, "debit", 25000.0), _ids(conn, s.id, "credit", 25000.0)
    for t in (debit, credit):
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) "
            "VALUES (?, ?, 'Transfers', 'Peer Transfer', ?, ?)", (f"a_{t}", t, confidence, label_source))
    conn.commit()
    return client, conn, k, s, debit, credit


class TestAnOpenCandidateNeedsTheUser:
    @pytest.mark.parametrize("source", ["rule", "llm", "rag_direct", "rag_prompted", "learned_rule", "policy"])
    def test_both_sides_are_in_teach_me_even_with_a_confident_machine_label(self, api, source):
        client, conn, k, s, debit, credit = _neft_pair(api, source, 0.99)
        queued = {i["transaction_id"] for i in client.get("/api/annotations/review-queue").json()}
        assert {debit, credit} <= queued
        assert client.get("/api/annotations/review-queue/counts").json()["unsorted"] == 2

    def test_the_card_offers_the_pair_first_and_the_existing_guess_next(self, api):
        client, conn, k, s, debit, credit = _neft_pair(api)
        item = next(i for i in client.get("/api/annotations/review-queue").json() if i["transaction_id"] == debit)
        first, second = item["suggestions"][:2]
        assert first["pair_with"] == credit
        assert (second["category"], second["subcategory"]) == ("Transfers", "Peer Transfer")

    def test_every_unsorted_figure_agrees(self, api):
        client, conn, k, s, debit, credit = _neft_pair(api)
        counts = client.get("/api/annotations/review-queue/counts").json()
        assert counts["unsorted"] == 2 and counts["guesses"] == 0
        listed = client.get("/api/transactions", params={"category": "Uncategorized", "limit": 50}).json()
        listed = listed["items"] if isinstance(listed, dict) else listed
        assert {t["id"] for t in listed} == {debit, credit}
        # The Dashboard's figure counts money out, so the debit; the credit is in Teach Me.
        month = client.get("/api/insights", params={"month": "2026-10"}).json()
        assert month["unexplained"] == {"count": 1, "total": 25000.0}
        statements = {r["id"]: r for r in client.get("/api/statements").json()}
        assert statements[k.id]["sorted_count"] == 0 and statements[s.id]["sorted_count"] == 0

    def test_coverage_does_not_count_them_as_sorted(self, api):
        from finance_copilot.db.queries.annotations import sorted_payment_coverage

        client, conn, *_ = _neft_pair(api)
        assert sorted_payment_coverage(conn, THRESHOLD, "m")[1] == 0

    def test_pairing_it_takes_both_out_of_every_unsorted_figure(self, api):
        client, conn, k, s, debit, credit = _neft_pair(api)
        assert client.post("/api/transfers/pair", json={"transaction_id": debit, "with_transaction_id": credit}).status_code == 200
        assert client.get("/api/annotations/review-queue/counts").json()["unsorted"] == 0
        assert client.get("/api/insights", params={"month": "2026-10"}).json()["unexplained"] == {"count": 0, "total": 0}

    def test_answering_one_side_as_something_else_releases_the_other(self, api):
        client, conn, k, s, debit, credit = _neft_pair(api)
        body = {"transaction_ids": [debit], "label": {"category": "Transfers", "subcategory": "Peer Transfer"}}
        client.post("/api/annotations/bulk-label", json=body)
        # the debit is the user's now and not Self Transfers, so the pair is no candidate any more
        assert client.get("/api/annotations/review-queue/counts").json()["unsorted"] == 0

    @pytest.mark.parametrize("source", ["manual", "imported", "user_rule"])
    def test_a_row_the_user_labelled_is_not_pulled_in_by_its_own_candidate(self, conn, source):
        _out(conn, "d", description="NEFT/RAMESH", ann=(source, "Self Transfers", "Self Transfer"))
        _in(conn, "c1", account=SBI)
        _in(conn, "c2", account=HDFC)  # ambiguous: nothing pairs
        assert pair_transfers(conn) == []
        queued = {i["transaction_id"] for i in list_review_queue(conn, THRESHOLD)}
        assert queued == {"c1", "c2"}
        assert queue_counts(conn, THRESHOLD)["unsorted"] == 2

    def test_a_machine_labelled_credit_is_pulled_in_by_a_candidate_the_user_has_not_labelled(self, conn):
        _out(conn, "d", description="NEFT/RAMESH", ann=("manual", "Self Transfers", "Self Transfer"))
        _in(conn, "c1", account=SBI, ann=("llm", "Income", "Other Income"))
        _in(conn, "c2", account=HDFC)
        assert pair_transfers(conn) == []
        assert {i["transaction_id"] for i in list_review_queue(conn, THRESHOLD)} == {"c1", "c2"}

    def test_nothing_changes_for_payments_without_a_candidate(self, conn):
        _out(conn, "d", description="NEFT/RAMESH", ann=("llm", "Shopping", "Online Shopping"))
        _in(conn, "c", day=20, ann=("llm", "Income", "Other Income"))  # a candidate by amount but 8 days off
        assert list_review_queue(conn, THRESHOLD) == []

    def test_the_stored_confidence_is_not_touched(self, api):
        client, conn, k, s, debit, credit = _neft_pair(api, confidence=0.97)
        client.get("/api/annotations/review-queue")
        assert [r[0] for r in conn.execute("SELECT confidence FROM annotations")] == [0.97, 0.97]


class TestTheCandidateDefinitionIsOneThing:
    """The Python finder and the SQL behind "unsorted" read one definition; check they agree on the edges."""

    def _agree(self, conn):
        from finance_copilot.db.queries.transfers import has_open_candidate_sql

        wanted = [r["id"] for r in conn.execute("SELECT id FROM transactions")]
        sql = {r["id"] for r in conn.execute(f"SELECT t.id FROM transactions t WHERE {has_open_candidate_sql('t')}")}
        assert sql == set(transfer_candidates(conn, wanted))
        return sql

    def test_in_and_out_of_the_window(self, conn):
        _out(conn, "d1", day=10, amount=100.0)
        _in(conn, "c1", day=13, amount=100.0)
        _out(conn, "d2", day=10, amount=200.0)
        _in(conn, "c2", day=14, amount=200.0)
        assert self._agree(conn) == {"d1", "c1"}

    def test_same_account_amount_to_the_paisa_and_direction(self, conn):
        _out(conn, "d1", account=KOTAK, amount=100.0)
        _in(conn, "c1", account=KOTAK, amount=100.0)
        _out(conn, "d2", account=KOTAK, amount=100.01)
        _in(conn, "c2", account=SBI, amount=100.0)
        _out(conn, "d3", account=KOTAK, amount=300.0)
        _out(conn, "d4", account=SBI, amount=300.0)
        assert self._agree(conn) == {"d1", "c2"}

    def test_labels_links_and_unpaired(self, conn):
        _out(conn, "d1", amount=100.0, ann=("manual", "Food & Dining"))
        _in(conn, "c1", amount=100.0)
        _out(conn, "d2", amount=200.0, ann=("user_rule", "Food & Dining"))
        _in(conn, "c2", amount=200.0)
        _out(conn, "d3", amount=300.0, ann=("imported", "Self Transfers"))
        _in(conn, "c3", amount=300.0)
        _out(conn, "d4", amount=400.0)
        _in(conn, "c4", amount=400.0)
        conn.execute("INSERT INTO transfer_unpaired (txn_a, txn_b) VALUES ('c4', 'd4')")
        _out(conn, "d5", amount=500.0)
        _in(conn, "c5", amount=500.0)
        conn.execute("INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES ('l', 'c5', 'd5', 'transfer')")
        _out(conn, "d6", amount=600.0)
        _in(conn, "c6", amount=600.0)
        conn.execute("INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES ('l2', 'c6', 'd6', 'refund')")
        conn.commit()
        assert self._agree(conn) == {"d3", "c3", "d6", "c6"}

    def test_the_query_uses_the_amount_index(self, conn):
        from finance_copilot.db.queries.transfers import has_open_candidate_sql

        plan = " ".join(
            str(tuple(r)) for r in conn.execute(
                f"EXPLAIN QUERY PLAN SELECT t.id FROM transactions t WHERE {has_open_candidate_sql('t')}"))
        assert "idx_txn_amount" in plan


# 5. unpair names what went back to Teach Me ----------------------------------------------------------


class TestUnpairSaysWhatWaitsInTeachMe:
    def test_both_payments_when_both_pair_labels_went(self, paired):
        client, conn, k, s, debit, credit = paired
        body = client.post("/api/transfers/unpair", json={"transaction_id": debit}).json()
        assert sorted(body["to_sort"]) == sorted([debit, credit])

    def test_only_the_side_without_the_users_own_label(self, api):
        client, conn, debit, credit = _manual_paired(api)
        body = client.post("/api/transfers/unpair", json={"transaction_id": credit}).json()
        assert body["to_sort"] == [credit]
        assert sorted(body["unpaired"]) == sorted([debit, credit])


# 6. removing the sample leaves no orphaned pair label -------------------------------------------------


class TestRemovingTheSampleFreesARealPayment:
    def test_a_real_credit_paired_with_a_sample_debit_goes_back_to_to_sort(self, conn):
        from finance_copilot import demo_seed
        from finance_copilot.db.queries.transfers import unpair

        demo_seed.seed(conn, date(2026, 11, 18))
        link = conn.execute("SELECT txn_a, txn_b FROM transaction_links WHERE link_type = 'transfer' ORDER BY txn_a DESC LIMIT 1").fetchone()
        debit = next(t for t in link if conn.execute("SELECT debit_credit FROM transactions WHERE id = ?", (t,)).fetchone()[0] == "debit")
        day = conn.execute("SELECT txn_date FROM transactions WHERE id = ?", (debit,)).fetchone()[0]
        unpair(conn, debit, remember=True)
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES ('real', 'hdfc', 'XXXX9999')")
        conn.execute("INSERT INTO statements (id, bank_name, parser_version, statement_month, account_id) VALUES ('real_s', 'hdfc', '1', '2026-10', 'real')")
        conn.execute("INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) VALUES ('real_c', 'real_s', ?, 10000, 'credit', 'NEFT/CR/SOMEONE')", (day,))
        conn.commit()
        assert pair_transfers(conn) == [(debit, "real_c")]
        demo_seed.wipe(conn)
        conn.commit()
        assert _annotation(conn, "real_c") is None
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM transfer_unpaired").fetchone()[0] == 0


# 7. the signs ------------------------------------------------------------------------------------------


class TestTheSignsAreStricter:
    @pytest.mark.parametrize("line", [
        "CASH WDL CHQ 000123 SELF",
        "ATM CASH SELF",
        "CHQ WDL SELF 4521",
        "NEFT CASH/SELF",
    ])
    def test_self_on_a_cash_withdrawal_line_is_not_a_sign(self, conn, line):
        _out(conn, amount=10000.0, description=line)
        _in(conn, amount=10000.0, description="NEFT/CR/RAMESH SHARMA/SALARY ADV")
        assert pair_transfers(conn) == []

    def test_self_on_a_line_that_only_contains_cash_as_part_of_a_word_still_counts(self, conn):
        _out(conn, description="IMPS/CASHFREE/SELF")
        _in(conn)
        assert pair_transfers(conn) == [("d", "c")]

    def test_own_on_a_cash_line_still_counts(self, conn):
        """Only the word SELF is dropped on a cash line, own account words stay."""
        _out(conn, description="CASH DEP TO OWN ACCOUNT")
        _in(conn)
        assert pair_transfers(conn) == [("d", "c")]

    @pytest.mark.parametrize("line", [
        "UPI/OWN_SHOP@ybl/412345678901/UPI",
        "UPI/SELF@okaxis/412345678901/UPI",
        "UPI/MY.OWN@paytm/412345678901",
        "UPI/OWN-STORE@ybl",
    ])
    def test_a_word_inside_a_upi_handle_is_not_a_sign(self, conn, line):
        _out(conn, description=line)
        _in(conn)
        assert pair_transfers(conn) == []

    def test_a_word_next_to_a_handle_still_counts(self, conn):
        _out(conn, description="UPI/TO SELF/shop@ybl")
        _in(conn)
        assert pair_transfers(conn) == [("d", "c")]

    def test_underscore_is_not_a_word_boundary(self, conn):
        _out(conn, description="NEFT/OWN_SHOP")
        _in(conn)
        assert pair_transfers(conn) == []

    @pytest.mark.parametrize("line", [
        "CHQ NO 2222 RENT",
        "NEFT/RAMESH/ 2222 ",
        "UPI REF 2222 PAID",
    ])
    def test_a_bare_four_digit_run_is_not_a_sign(self, conn, line):
        _out(conn, description=line)
        _in(conn)
        assert pair_transfers(conn) == []

    @pytest.mark.parametrize("line", [
        "IMPS/XX2222",
        "IMPS/xx2222",
        "IMPS/**2222",
        "IMPS/X2222",
        "NEFT/A/C 2222",
        "NEFT/A/C-2222",
        "NEFT/AC2222",
        "NEFT/AC 2222",
        "NEFT/ACCOUNT 2222",
        "NEFT/ACCOUNT: 2222",
        "NEFT/XXXXXXXX2222",
        "NEFT/XXXX XXXX 2222",
        "NEFT/TO 000000002222",
    ])
    def test_the_last_four_after_a_mask_or_an_account_word_or_the_full_number_is_a_sign(self, conn, line):
        conn.execute("UPDATE accounts SET account_ref = '000000002222' WHERE id = ?", (SBI,))
        conn.commit()
        _out(conn, description=line)
        _in(conn)
        assert pair_transfers(conn) == [("d", "c")]

    def test_the_full_stored_digits_count_alone(self, conn):
        conn.execute("UPDATE accounts SET account_ref = '123456782222' WHERE id = ?", (SBI,))
        conn.commit()
        _out(conn, description="NEFT/RAMESH 123456782222")
        _in(conn)
        assert pair_transfers(conn) == [("d", "c")]

    def test_a_longer_run_ending_in_the_digits_is_not_the_number(self, conn):
        conn.execute("UPDATE accounts SET account_ref = '123456782222' WHERE id = ?", (SBI,))
        conn.commit()
        _out(conn, description="UPI/412345678901123456782222")
        _in(conn)
        assert pair_transfers(conn) == []

    def test_the_self_cheque_with_a_neft_credit_does_not_pair(self, conn):
        _out(conn, amount=10000.0, description="CHQ WDL CHQ NO 3521 SELF")
        _in(conn, amount=10000.0, description="NEFT/CR/RAMESH SHARMA/SALARY ADV")
        conn.execute("UPDATE accounts SET account_ref = 'XXXXXXXX3521' WHERE id = ?", (SBI,))
        conn.commit()
        assert pair_transfers(conn) == []
        assert _links(conn) == []


# 11. a user rule's label is the user's intent ----------------------------------------------------------


class TestAUserRuleLabelBlocksPairingLikeAHandLabel:
    def test_a_credit_a_user_rule_sorted_as_something_else_never_pairs(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT")
        _in(conn, ann=("user_rule", "Income", "Other Income"))
        assert pair_transfers(conn) == []
        assert transfer_candidates(conn, ["d", "c"]) == {}

    def test_a_side_a_user_rule_sorted_as_self_transfers_pairs_and_keeps_its_label(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT", ann=("user_rule", "Self Transfers", "Self Transfer"))
        _in(conn)
        assert pair_transfers(conn) == [("d", "c")]
        assert _annotation(conn, "d")["source"] == "user_rule"
        assert _annotation(conn, "c")["source"] == "pair"

    def test_the_route_refuses_to_pair_over_it(self, api):
        client, conn = api
        k = _ingest(conn, _Parser("kotak", "XXXXXXXX1111", [_t(20, 7000.0, "debit", "NEFT/A")]), "k")
        s = _ingest(conn, _Parser("sbi", "XXXXXXXX2222", [_t(20, 7000.0, "credit", "NEFT/B")]), "s")
        debit, credit = _ids(conn, k.id, "debit", 7000.0), _ids(conn, s.id, "credit", 7000.0)
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) "
            "VALUES ('u', ?, 'Income', 'Other Income', 1.0, 'user_rule')", (credit,))
        conn.commit()
        assert client.post("/api/transfers/pair", json={"transaction_id": debit, "with_transaction_id": credit}).status_code == 409


# 8. the routes read and snapshot inside the write transaction ----------------------------------------------


class TestTheRoutesSnapshotInsideTheWriteLock:
    @pytest.mark.parametrize("which", ["pair", "unpair"])
    def test_the_undo_snapshot_is_taken_with_the_write_lock_held(self, api, monkeypatch, which):
        from finance_copilot.api.routes import transfers

        client, conn = api
        k = _ingest(conn, _Parser("kotak", "XXXXXXXX1111", [_t(20, 7000.0, "debit", "NEFT/A")]), "k")
        s = _ingest(conn, _Parser("sbi", "XXXXXXXX2222", [_t(20, 7000.0, "credit", "NEFT/B")]), "s")
        debit, credit = _ids(conn, k.id, "debit", 7000.0), _ids(conn, s.id, "credit", 7000.0)
        if which == "unpair":
            client.post("/api/transfers/pair", json={"transaction_id": debit, "with_transaction_id": credit})
        seen = []
        real = transfers.UndoCapture

        class Spy(real):
            def __init__(self, c, ids):
                seen.append(c.in_transaction)
                super().__init__(c, ids)

        monkeypatch.setattr(transfers, "UndoCapture", Spy)
        body = {"transaction_id": debit, "with_transaction_id": credit} if which == "pair" else {"transaction_id": debit}
        assert client.post(f"/api/transfers/{which}", json=body).status_code == 200
        assert seen == [True]


# 9. ingest retries once on a busy database; deleting re-runs pairing ----------------------------------------


class TestIngestAndDelete:
    def test_a_busy_database_is_retried_once(self, api, monkeypatch):
        from finance_copilot.pipeline import ingest

        client, conn = api
        calls = []
        real = ingest.pair_transfers

        def flaky(c):
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            return real(c)

        monkeypatch.setattr(ingest, "pair_transfers", flaky)
        monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
        _ingest(conn, _kotak(), "k")
        s = _ingest(conn, _sbi(), "s")
        assert len(calls) == 3  # first upload: one try; second: busy, then retried
        assert s.transfers_paired == 1

    def test_a_second_busy_try_is_logged_and_the_upload_stands(self, api, monkeypatch):
        from finance_copilot.pipeline import ingest

        client, conn = api

        def busy(c):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(ingest, "pair_transfers", busy)
        monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
        s = _ingest(conn, _sbi(), "s")
        assert s.transfers_paired == 0
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] > 0

    def test_deleting_a_statement_lets_a_pair_that_was_ambiguous_become_unambiguous(self, api):
        client, conn = api
        s = _ingest(conn, _Parser("sbi", "XXXXXXXX2222", [_t(10, 900.0, "credit", "NEFT/B")]), "s")
        h = _ingest(conn, _Parser("hdfc", "XXXXXXXX3333", [_t(11, 900.0, "credit", "NEFT/C")]), "h")
        _ingest(conn, _Parser("kotak", "XXXXXXXX1111", [_t(10, 900.0, "debit", "IMPS/TO OWN ACCOUNT")]), "k")
        assert conn.execute("SELECT COUNT(*) FROM transaction_links").fetchone()[0] == 0  # ambiguous
        assert client.delete(f"/api/statements/{h.id}").status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM transaction_links WHERE link_type = 'transfer'").fetchone()[0] == 1
        assert _ids(conn, s.id, "credit", 900.0) in {r[0] for r in conn.execute("SELECT txn_a FROM transaction_links UNION SELECT txn_b FROM transaction_links")}


# The unpair answer says which account each payment is in -------------------------------------------------


def test_the_unpair_body_names_each_payments_account(paired):
    client, conn, k, s, debit, credit = paired
    body = client.post("/api/transfers/unpair", json={"transaction_id": debit}).json()
    json.dumps(body)
    assert set(body) == {"unpaired", "to_sort", "account_labels", "undo_token"}
    assert body["account_labels"] == {debit: "Kotak ··1111", credit: "SBI ··2222"}
