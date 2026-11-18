"""Pairing (R3 part C): a transfer between your own accounts is not spending.

Every branch of the rule, each sign of a self transfer on its own, and what
pairing writes. The routes, the pipeline guard and the money math have their own
classes below."""
from __future__ import annotations

import sqlite3

import pytest

from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.transfers import WINDOW_DAYS
from finance_copilot.pipeline.pairing import (
    SELF_TRANSFER_WORDS,
    pair_transfers,
    transfer_candidates,
)

KOTAK = "acc_kotak"
SBI = "acc_sbi"
HDFC = "acc_hdfc"


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_db(c)
    for account_id, bank, ref in ((KOTAK, "kotak", "XXXXXXXX1111"), (SBI, "sbi", "XXXXXXXX2222"), (HDFC, "hdfc", "XXXXXXXX3333")):
        c.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES (?, ?, ?)", (account_id, bank, ref))
        c.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month, account_id, account_ref) "
            "VALUES (?, ?, '1', '2026-10', ?, ?)", (f"s_{account_id}", bank, account_id, ref))
    c.commit()
    return c


def _txn(conn, txn_id, account, day, amount, dc, description, *, ann=None, key=None, upi_meta=None):
    """ann is (source, category) or (source, category, subcategory)."""
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, "
        "counterparty_key, upi_meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (txn_id, f"s_{account}", f"2026-10-{day:02d}", amount, dc, description, key, upi_meta))
    if ann:
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) "
            "VALUES (?, ?, ?, ?, 0.9, ?)",
            (f"a_{txn_id}", txn_id, ann[1], ann[2] if len(ann) > 2 else None, ann[0]))
    conn.commit()
    return txn_id


def _out(conn, txn_id="d", account=KOTAK, day=28, amount=5000.0, description="NEFT/RAMESH SHARMA", **kw):
    return _txn(conn, txn_id, account, day, amount, "debit", description, **kw)


def _in(conn, txn_id="c", account=SBI, day=28, amount=5000.0, description="NEFT/CREDIT", **kw):
    return _txn(conn, txn_id, account, day, amount, "credit", description, **kw)


def _links(conn):
    return [tuple(r) for r in conn.execute("SELECT txn_a, txn_b, link_type FROM transaction_links")]


def _annotation(conn, txn_id):
    row = conn.execute("SELECT * FROM annotations WHERE transaction_id = ?", (txn_id,)).fetchone()
    return dict(row) if row else None


class TestWhatAPairWrites:
    def test_a_signed_unambiguous_pair_is_linked_and_both_sides_sorted_as_self_transfers(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT XX2222/SAVINGS")
        _in(conn, day=29)
        assert pair_transfers(conn) == [("d", "c")]
        assert _links(conn) == [("c", "d", "transfer")]  # txn_a < txn_b
        for txn_id in ("d", "c"):
            ann = _annotation(conn, txn_id)
            assert (ann["category"], ann["subcategory"], ann["source"]) == ("Self Transfers", "Self Transfer", "pair")
            assert ann["category_id"] == "cat_self_transfers" and ann["subcategory_id"] == "cat_tx_self"
            assert ann["confidence"] == 1.0

    def test_it_changes_no_amount_or_date_or_description(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT XX2222/SAVINGS")
        _in(conn, day=29)
        before = [tuple(r) for r in conn.execute("SELECT * FROM transactions ORDER BY id")]
        pair_transfers(conn)
        assert [tuple(r) for r in conn.execute("SELECT * FROM transactions ORDER BY id")] == before

    def test_running_it_twice_changes_nothing(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT XX2222/SAVINGS")
        _in(conn, day=29)
        assert pair_transfers(conn)
        snapshot = ([tuple(r) for r in conn.execute("SELECT * FROM transaction_links")],
                    [tuple(r) for r in conn.execute("SELECT * FROM annotations ORDER BY id")])
        assert pair_transfers(conn) == []
        assert ([tuple(r) for r in conn.execute("SELECT * FROM transaction_links")],
                [tuple(r) for r in conn.execute("SELECT * FROM annotations ORDER BY id")]) == snapshot

    def test_a_machine_label_on_either_side_is_replaced(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT XX2222", ann=("llm", "Shopping"))
        _in(conn, ann=("rule", "Income"))
        pair_transfers(conn)
        assert _annotation(conn, "d")["source"] == "pair" and _annotation(conn, "c")["source"] == "pair"
        assert _annotation(conn, "c")["category"] == "Self Transfers"

    def test_a_skipped_payment_comes_back_into_the_queue_once_paired(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT XX2222")
        _in(conn)
        conn.execute("UPDATE transactions SET review_snoozed_at = datetime('now')")
        conn.commit()
        pair_transfers(conn)
        assert [r[0] for r in conn.execute("SELECT review_snoozed_at FROM transactions")] == [None, None]


class TestTheRule:
    @pytest.mark.parametrize("gap,pairs", [(0, True), (1, True), (3, True), (4, False)])
    def test_dates_at_most_three_days_apart(self, conn, gap, pairs):
        assert WINDOW_DAYS == 3
        _out(conn, day=10, description="IMPS/TO OWN ACCOUNT")
        _in(conn, day=10 + gap)
        assert bool(pair_transfers(conn)) is pairs

    def test_the_credit_may_come_first(self, conn):
        _out(conn, day=12, description="IMPS/TO OWN ACCOUNT")
        _in(conn, day=10)
        assert pair_transfers(conn) == [("d", "c")]

    def test_amounts_must_be_equal_to_the_paisa(self, conn):
        _out(conn, amount=5000.00, description="IMPS/TO OWN ACCOUNT")
        _in(conn, amount=5000.01)
        assert pair_transfers(conn) == []

    def test_amounts_equal_after_float_noise_still_pair(self, conn):
        _out(conn, amount=1234.56, description="IMPS/TO OWN ACCOUNT")
        _in(conn, amount=1234.5600000001)
        assert pair_transfers(conn) == [("d", "c")]

    def test_two_payments_in_one_account_never_pair(self, conn):
        _out(conn, account=KOTAK, description="IMPS/TO OWN ACCOUNT")
        _in(conn, account=KOTAK)
        assert pair_transfers(conn) == []
        assert transfer_candidates(conn, ["d", "c"]) == {}

    def test_a_debit_pairs_with_a_credit_not_another_debit(self, conn):
        _out(conn, "d1", account=KOTAK, description="IMPS/TO OWN ACCOUNT")
        _out(conn, "d2", account=SBI, description="IMPS/TO OWN ACCOUNT")
        assert pair_transfers(conn) == []

    def test_a_hand_label_in_another_category_on_either_side_never_pairs(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT", ann=("manual", "Gifts & Donations"))
        _in(conn)
        assert pair_transfers(conn) == []
        assert _annotation(conn, "c") is None

    @pytest.mark.parametrize("source", ["manual", "imported"])
    def test_a_credit_the_user_labelled_something_else_never_pairs(self, conn, source):
        _out(conn, description="IMPS/TO OWN ACCOUNT")
        _in(conn, ann=(source, "Income"))
        assert pair_transfers(conn) == []

    @pytest.mark.parametrize("source", ["manual", "imported"])
    def test_a_side_the_user_sorted_as_self_transfers_may_pair_and_keeps_their_label(self, conn, source):
        _out(conn, description="IMPS/P2P/TRANSFER/TO SBI", ann=(source, "Self Transfers", "Self Transfer"))
        _in(conn)
        assert pair_transfers(conn) == [("d", "c")]
        assert _annotation(conn, "d")["source"] == source  # their word stays theirs
        assert _annotation(conn, "c")["source"] == "pair"

    def test_a_pair_the_user_took_apart_is_never_made_again(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT")
        _in(conn)
        conn.execute("INSERT INTO transfer_unpaired (txn_a, txn_b) VALUES ('c', 'd')")
        conn.commit()
        assert pair_transfers(conn) == []
        assert transfer_candidates(conn, ["d"]) == {}

    def test_a_payment_already_in_a_transfer_is_left_alone(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT")
        _in(conn, "c1")
        _in(conn, "c2", account=HDFC)
        conn.execute("INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES ('l', 'c1', 'd', 'transfer')")
        conn.commit()
        assert pair_transfers(conn) == []

    def test_a_refund_or_split_link_is_not_a_transfer(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT")
        _in(conn)
        conn.execute("INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES ('l', 'c', 'd', 'refund')")
        conn.commit()
        assert pair_transfers(conn) == [("d", "c")]

    def test_with_one_account_it_does_nothing(self, conn):
        conn.execute("DELETE FROM statements WHERE account_id != ?", (KOTAK,))
        conn.execute("DELETE FROM accounts WHERE id != ?", (KOTAK,))
        _out(conn, description="IMPS/TO OWN ACCOUNT")
        assert pair_transfers(conn) == []


class TestAmbiguity:
    def test_one_debit_and_two_credits_of_the_same_amount_pair_nothing_even_with_a_sign(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT")
        _in(conn, "c1", account=SBI)
        _in(conn, "c2", account=HDFC)
        assert pair_transfers(conn) == []
        assert _links(conn) == []
        assert _annotation(conn, "d") is None and _annotation(conn, "c1") is None

    def test_two_debits_for_one_credit_pair_nothing(self, conn):
        _out(conn, "d1", account=KOTAK, description="IMPS/TO OWN ACCOUNT")
        _out(conn, "d2", account=HDFC, description="IMPS/TO OWN ACCOUNT")
        _in(conn)
        assert pair_transfers(conn) == []

    def test_two_credits_in_one_account_still_make_the_debit_ambiguous(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT")
        _in(conn, "c1", day=27)
        _in(conn, "c2", day=29)
        assert pair_transfers(conn) == []

    def test_a_second_credit_outside_the_window_does_not_make_it_ambiguous(self, conn):
        _out(conn, day=10, description="IMPS/TO OWN ACCOUNT")
        _in(conn, "c1", day=11)
        _in(conn, "c2", day=20)
        assert pair_transfers(conn) == [("d", "c1")]

    def test_a_second_credit_the_user_labelled_something_else_does_not_make_it_ambiguous(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT")
        _in(conn, "c1")
        _in(conn, "c2", account=HDFC, ann=("manual", "Income"))
        assert pair_transfers(conn) == [("d", "c1")]

    def test_ambiguous_payments_are_candidates_for_teach_me(self, conn):
        _out(conn)
        _in(conn, "c1", account=SBI)
        _in(conn, "c2", account=HDFC, day=27)
        found = transfer_candidates(conn, ["d"])["d"]
        assert [f["transaction_id"] for f in found] == ["c1", "c2"]  # same day first, then the day before
        assert {f["matches"] for f in found} == {2}
        assert found[0]["gap_days"] <= found[1]["gap_days"]


class TestTheSignsOfASelfTransfer:
    """Amount and date alone are not enough; each sign is enough alone."""

    def test_without_a_sign_nothing_is_paired_and_the_candidate_is_offered_both_ways(self, conn):
        _out(conn, description="NEFT/RAMESH SHARMA")
        _in(conn, description="NEFT/SOMEONE ELSE", day=30)
        assert pair_transfers(conn) == []
        assert _links(conn) == [] and _annotation(conn, "d") is None and _annotation(conn, "c") is None
        d = transfer_candidates(conn, ["d", "c"])
        assert d["d"][0]["transaction_id"] == "c" and d["d"][0]["direction"] == "to"
        assert d["d"][0]["account_label"] == "SBI ··2222"
        assert d["c"][0]["transaction_id"] == "d" and d["c"][0]["direction"] == "from"
        assert d["c"][0]["account_label"] == "Kotak ··1111"
        assert d["d"][0]["txn_date"] == "2026-10-30" and d["d"][0]["gap_days"] == 2 and d["d"][0]["matches"] == 1

    @pytest.mark.parametrize("line", [
        "IMPS/TO OWN ACCOUNT XX2222/SAVINGS",   # masked
        "NEFT/A/C 2222/SBI",                     # last four after an account word
        "NEFT/XXXXXXXX2222",                     # as stored
        "NEFT/TO A/C XXXX XXXX 2222",            # with spaces, the last run counts
    ])
    def test_the_other_accounts_number_in_the_debit_line(self, conn, line):
        _out(conn, description=line.replace("OWN", "THEIR"))
        _in(conn)
        assert pair_transfers(conn) == [("d", "c")]

    def test_the_other_accounts_number_in_the_credit_line(self, conn):
        _out(conn, description="NEFT/RAMESH SHARMA")
        _in(conn, description="NEFT/FROM A/C XX1111/RAMESH")
        assert pair_transfers(conn) == [("d", "c")]

    def test_a_number_that_is_not_the_other_accounts_is_not_a_sign(self, conn):
        _out(conn, description="NEFT/RAMESH A/C XX9999")
        _in(conn)
        assert pair_transfers(conn) == []

    def test_the_senders_own_number_is_not_a_sign(self, conn):
        """The debit line printing its own account's number says nothing about the other side."""
        _out(conn, description="NEFT/FROM A/C XX1111/RAMESH")
        _in(conn)
        assert pair_transfers(conn) == []

    def test_a_long_reference_that_ends_in_the_same_digits_is_not_a_sign(self, conn):
        _out(conn, description="UPI/RAMESH/412345672222/UPI")
        _in(conn)
        assert pair_transfers(conn) == []

    def test_a_short_number_never_counts(self, conn):
        conn.execute("UPDATE accounts SET account_ref = 'XX22' WHERE id = ?", (SBI,))
        conn.commit()
        _out(conn, description="NEFT/RAMESH 22")
        _in(conn)
        assert pair_transfers(conn) == []

    @pytest.mark.parametrize("source", ["manual", "imported", "llm", "rule", "learned_rule", "rag_direct", "policy"])
    def test_a_self_transfers_label_from_any_source_on_the_debit(self, conn, source):
        _out(conn, ann=(source, "Self Transfers", "Self Transfer"))
        _in(conn)
        assert pair_transfers(conn) == [("d", "c")]

    def test_a_self_transfers_label_on_the_credit(self, conn):
        _out(conn)
        _in(conn, ann=("llm", "Self Transfers", "Self Transfer"))
        assert pair_transfers(conn) == [("d", "c")]

    def test_a_payee_marked_self_in_people(self, conn):
        conn.execute("INSERT INTO people (id, name, upi, relationship) VALUES ('p', 'My SBI', 'abhi@oksbi', 'self')")
        conn.commit()
        _out(conn, description="UPI/ABHI@OKSBI/412345678901/UPI", key="ABHI@OKSBI",
             upi_meta='{"vpa": "abhi@oksbi"}')
        _in(conn)
        assert pair_transfers(conn) == [("d", "c")]

    def test_a_person_who_is_not_self_is_not_a_sign(self, conn):
        conn.execute("INSERT INTO people (id, name, upi, relationship) VALUES ('p', 'Rahul', 'rahul@ybl', 'friend')")
        conn.commit()
        _out(conn, description="UPI/RAHUL@YBL/412345678901/UPI", key="RAHUL@YBL", upi_meta='{"vpa": "rahul@ybl"}')
        _in(conn)
        assert pair_transfers(conn) == []

    @pytest.mark.parametrize("line", [
        "IMPS/P2P/TRANSFER/TO OWN SBI SAVINGS",
        "NEFT/own",
        "NEFT/SELF",
        "NEFT/CHEQUE SELF",
        "IMPS/to self/savings",
        "IMPS/FROM SELF",
        "IMPS/OWN-ACCOUNT",
        "IMPS/OWN  ACCOUNT",
    ])
    def test_a_self_transfer_word_in_either_line(self, conn, line):
        _out(conn, description=line)
        _in(conn)
        assert pair_transfers(conn) == [("d", "c")]

    @pytest.mark.parametrize("line", ["IMPS/DOWNTOWN CAFE", "NEFT/GROWN UP LTD", "NEFT/SELFIE STUDIO", "NEFT/OWNER RAMESH", "NEFT/SHELF"])
    def test_a_word_that_only_contains_one_is_not_a_sign(self, conn, line):
        _out(conn, description=line)
        _in(conn, description=line)
        assert pair_transfers(conn) == []

    def test_the_words_live_in_one_list(self):
        assert SELF_TRANSFER_WORDS == ("own", "self", "own account", "to self", "from self")


class TestEachPairStandsOnItsOwn:
    def test_two_transfers_of_the_same_amount_a_month_apart_both_pair(self, conn):
        _out(conn, "d1", day=5, description="IMPS/TO OWN ACCOUNT")
        _in(conn, "c1", day=5)
        _out(conn, "d2", day=25, description="IMPS/TO OWN ACCOUNT")
        _in(conn, "c2", day=25)
        assert sorted(pair_transfers(conn)) == [("d1", "c1"), ("d2", "c2")]

class TestPairingNeverRedecidesAPair:
    """The sorting pipeline must leave a pair annotation alone, on every path that
    re-evaluates labels."""

    def _paired(self, conn):
        _out(conn, description="IMPS/TO OWN ACCOUNT")
        _in(conn)
        pair_transfers(conn)
        return {t: _annotation(conn, t) for t in ("d", "c")}

    def test_pair_is_trusted_but_not_verified_machine_model_or_reviewable(self):
        from finance_copilot.pipeline import sources

        assert "pair" in sources.TRUSTED_SOURCES
        for group in (sources.VERIFIED_SOURCES, sources.MACHINE_SOURCES, sources.MODEL_SOURCES, sources.REVIEWABLE_SOURCES):
            assert "pair" not in group

    def test_sorting_a_statement_skips_paired_payments(self, conn):
        before = self._paired(conn)
        from finance_copilot.pipeline.annotate import auto_annotate

        result = auto_annotate(conn, statement_id=f"s_{KOTAK}")
        assert result.total_processed == 0
        assert {t: _annotation(conn, t) for t in ("d", "c")} == before

    def test_sort_again_over_the_whole_ledger_skips_them_too(self, conn):
        before = self._paired(conn)
        from finance_copilot.pipeline.annotate import auto_annotate

        auto_annotate(conn)
        assert {t: _annotation(conn, t) for t in ("d", "c")} == before

    def test_reevaluating_machine_labels_leaves_them(self, conn):
        before = self._paired(conn)
        from finance_copilot.pipeline.annotate import (
            reevaluate_after_policy_change,
            reevaluate_after_teaching,
            reevaluate_machine_labels,
            rescore_review_queue,
        )

        assert reevaluate_machine_labels(conn, ["d", "c"], reason="teaching") == []
        assert reevaluate_machine_labels(conn, ["d", "c"], reason="policy", retrieval=False) == []
        reevaluate_after_teaching(conn, ["d"], candidates=["c"])
        rescore_review_queue(conn)
        reevaluate_after_policy_change(conn)
        assert {t: _annotation(conn, t) for t in ("d", "c")} == before

    def test_nothing_lists_them_as_waiting_for_review(self, conn):
        self._paired(conn)
        from finance_copilot.db.queries.annotations import list_review_queue, queue_counts

        assert list_review_queue(conn, 0.85) == []
        assert queue_counts(conn, 0.85)["unsorted"] == 0 and queue_counts(conn, 0.85)["guesses"] == 0

    def test_apply_to_similar_does_not_overwrite_them(self, conn):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from finance_copilot.api.deps import get_db
        from finance_copilot.main import app

        before = self._paired(conn)
        _out(conn, "d9", day=3, amount=77.0, description="IMPS/TO OWN ACCOUNT", ann=("manual", "Gifts & Donations"))
        app.dependency_overrides[get_db] = lambda: conn
        try:
            with patch("finance_copilot.api.routes.annotations._embed_in_background"):
                resp = TestClient(app).post(
                    f"/api/annotations/{_annotation(conn, 'd9')['id']}/apply-to-similar",
                    json={"transaction_ids": ["d", "c"]},
                )
        finally:
            app.dependency_overrides.pop(get_db, None)
        assert resp.status_code == 200
        assert (resp.json()["applied"], resp.json()["skipped"]) == (0, 2)
        assert {t: _annotation(conn, t) for t in ("d", "c")} == before
