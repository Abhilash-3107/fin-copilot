"""Every unsorted payment reaches the review queue (R1 PR C).

The queue is keyed by transaction and has three kinds of item: a machine label
below the confidence bar, a machine label that says Uncategorized, and a
payment with no annotation. A skip belongs to the payment, so it sticks for all
three.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from finance_copilot.db.connection import _iter_statements, get_connection, init_db
from finance_copilot.db.queries.annotations import count_snoozed, list_review_queue
from finance_copilot.pipeline.annotate import rescore_review_queue

MIGRATIONS = Path(__file__).parents[1] / "db" / "migrations"


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    init_db(c)
    c.execute("INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s', 't', '1', '2026-06')")
    c.commit()
    return c


@pytest.fixture
def client(conn):
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.main import app

    app.dependency_overrides[api_get_db] = lambda: conn
    with patch("finance_copilot.api.routes.annotations.embed_transaction", return_value=False), \
            patch("finance_copilot.api.routes.annotations.embed_transactions"), \
            patch("finance_copilot.api.routes.annotations._embed_in_background"):
        yield TestClient(app)
    app.dependency_overrides.pop(api_get_db, None)


def _txn(conn, txn_id, label=None, *, source="llm", confidence=0.95, dc="debit", amount=100.0, day=1):
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
        "VALUES (?, 's', ?, ?, ?, ?)",
        (txn_id, f"2026-06-{day:02d}", amount, dc, f"UPI/SHOP {txn_id}/12345678901{day}/UPI"),
    )
    if label is not None:
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, confidence, source) VALUES (?, ?, ?, ?, ?)",
            (f"a_{txn_id}", txn_id, label, confidence, source),
        )
    conn.commit()


def _counts(client):
    return client.get("/api/annotations/review-queue/counts").json()


def _queue(client):
    return {i["transaction_id"]: i for i in client.get("/api/annotations/review-queue").json()}


class TestQueueContents:
    def test_three_kinds_of_unsorted_payment_are_queued(self, client, conn):
        _txn(conn, "low", "Shopping", confidence=0.3)
        _txn(conn, "uncat", "Uncategorized", confidence=0.97)
        _txn(conn, "bare")
        _txn(conn, "bare_credit", dc="credit")
        queue = _queue(client)
        assert set(queue) == {"low", "uncat", "bare", "bare_credit"}
        assert queue["bare"]["annotation_id"] is None
        assert queue["bare"]["category"] is None
        assert queue["bare"]["confidence"] == 0
        assert queue["uncat"]["annotation_id"] == "a_uncat"

    def test_sorted_payments_stay_out(self, client, conn):
        _txn(conn, "good", "Shopping", confidence=0.95)
        _txn(conn, "rule", "Shopping", source="rule", confidence=1.0)
        assert _queue(client) == {}

    @pytest.mark.parametrize(
        "source", ["manual", "imported", "user_rule", "rule", "llm", "rag_direct", "learned_rule", "policy", "model"]
    )
    def test_an_uncategorized_label_is_queued_whatever_its_source_or_confidence(self, client, conn, source):
        """The queue is a superset of the unsorted definition: a human's own
        Uncategorized still counts as not sorted, so it must be reachable."""
        _txn(conn, "t", "Uncategorized", source=source, confidence=1.0)
        assert set(_queue(client)) == {"t"}

    @pytest.mark.parametrize("source", ["manual", "imported", "user_rule"])
    def test_a_human_label_with_a_real_category_is_never_queued(self, client, conn, source):
        _txn(conn, "t", "Shopping", source=source, confidence=0.1)
        assert _queue(client) == {}

    def test_items_group_by_payee_with_and_without_annotation(self, client, conn):
        for i, label in enumerate([None, "Uncategorized", "Shopping"]):
            conn.execute(
                "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
                "VALUES (?, 's', ?, 50, 'debit', ?, 'OBEROIFC')",
                (f"g{i}", f"2026-06-{i + 1:02d}", f"UPI/OBEROIFC/12345678901{i}/UPI"),
            )
            if label:
                conn.execute(
                    "INSERT INTO annotations (id, transaction_id, category, confidence, source) VALUES (?, ?, ?, ?, 'llm')",
                    (f"a_g{i}", f"g{i}", label, 0.2),
                )
        conn.commit()
        items = client.get("/api/annotations/review-queue").json()
        assert [i["transaction_id"] for i in items] == ["g0", "g1", "g2"]
        assert {i["group_size"] for i in items} == {3}

    def test_queue_matches_the_unsorted_definition(self, conn):
        """Everything the Dashboard counts as unsorted is in the queue (or set aside)."""
        from finance_copilot.db.queries.common import unsorted_sql

        _txn(conn, "a", "Uncategorized", confidence=0.9)
        _txn(conn, "b")
        _txn(conn, "c", "Shopping")
        _txn(conn, "manual_uncat", "Uncategorized", source="manual", confidence=1.0)
        _txn(conn, "imported_uncat", "Uncategorized", source="imported", confidence=1.0)
        _txn(conn, "rule_uncat", "Uncategorized", source="user_rule", confidence=1.0)
        _txn(conn, "credit", "Uncategorized", source="manual", confidence=1.0, dc="credit")
        unsorted = {
            r[0] for r in conn.execute(
                f"SELECT t.id FROM transactions t LEFT JOIN annotations a ON a.transaction_id = t.id WHERE {unsorted_sql('a')}"
            )
        }
        assert unsorted == {"a", "b", "manual_uncat", "imported_uncat", "rule_uncat", "credit"}
        assert {i["transaction_id"] for i in list_review_queue(conn, 0.85)} == unsorted


class TestAnsweringByHand:
    def test_an_unannotated_item_answered_by_hand_becomes_a_manual_annotation(self, client, conn):
        _txn(conn, "bare")
        resp = client.post("/api/annotations", json={
            "transaction_id": "bare", "category": "Shopping", "confidence": 1.0, "source": "manual",
        })
        assert resp.status_code == 201
        assert _queue(client) == {}
        row = conn.execute("SELECT category, source, confidence FROM annotations WHERE transaction_id = 'bare'").fetchone()
        assert tuple(row) == ("Shopping", "manual", 1.0)

    def test_a_group_answer_labels_unannotated_members_without_feedback(self, client, conn):
        _txn(conn, "bare", day=1)
        _txn(conn, "guess", "Uncategorized", confidence=0.2, day=2)
        resp = client.post("/api/annotations/bulk-label", json={
            "transaction_ids": ["bare", "guess"], "label": {"category": "Shopping"},
        })
        assert resp.json()["updated"] == 2
        assert _queue(client) == {}
        rows = {r[0]: tuple(r[1:]) for r in conn.execute("SELECT transaction_id, category, source FROM annotations")}
        assert rows == {"bare": ("Shopping", "manual"), "guess": ("Shopping", "manual")}
        # Only the model's own guess is feedback; the hand label on a bare row is not.
        stats = [dict(r) for r in conn.execute("SELECT source, category, confirmed, corrected, refined FROM feedback_stats")]
        assert stats == [{"source": "llm", "category": "Uncategorized", "confirmed": 0, "corrected": 1, "refined": 0}]

    def test_a_hand_label_on_a_bare_row_records_no_feedback(self, client, conn):
        _txn(conn, "bare")
        client.post("/api/annotations/bulk-label", json={"transaction_ids": ["bare"], "label": {"category": "Shopping"}})
        assert conn.execute("SELECT COUNT(*) FROM feedback_stats").fetchone()[0] == 0

    def test_a_group_answer_ignores_a_deleted_transaction(self, client, conn):
        _txn(conn, "bare")
        resp = client.post("/api/annotations/bulk-label", json={
            "transaction_ids": ["bare", "gone"], "label": {"category": "Shopping"},
        })
        assert resp.status_code == 200 and resp.json()["updated"] == 1


class TestSkipSticks:
    @pytest.mark.parametrize("kind", ["bare", "uncat", "low"])
    def test_skip_persists_across_requests_for_every_kind(self, client, conn, kind):
        {"bare": lambda: _txn(conn, "t"),
         "uncat": lambda: _txn(conn, "t", "Uncategorized", confidence=0.9),
         "low": lambda: _txn(conn, "t", "Shopping", confidence=0.3)}[kind]()
        assert "t" in _queue(client)
        assert client.post("/api/annotations/review-queue/skip", json={"transaction_id": "t"}).status_code == 200
        assert "t" not in _queue(client)
        assert "t" not in _queue(client)
        assert _counts(client)["set_aside"] == 1

    def test_unsnooze_restores_both_kinds(self, client, conn):
        _txn(conn, "bare")
        _txn(conn, "low", "Shopping", confidence=0.3)
        for t in ("bare", "low"):
            client.post("/api/annotations/review-queue/skip", json={"transaction_id": t})
        assert _queue(client) == {}
        assert client.post("/api/annotations/review-queue/unsnooze").json() == {"restored": 2}
        assert set(_queue(client)) == {"bare", "low"}
        assert _counts(client)["set_aside"] == 0

    def test_snoozed_count_is_right_after_skip_back_answer(self, client, conn):
        """AUDIT B2: skip, go back, answer. Nothing is left set aside."""
        _txn(conn, "t", "Uncategorized", confidence=0.9)
        client.post("/api/annotations/review-queue/skip", json={"transaction_id": "t"})
        client.patch("/api/annotations/a_t", json={"category": "Shopping"})
        assert _counts(client)["set_aside"] == 0
        assert client.post("/api/annotations/review-queue/unsnooze").json() == {"restored": 0}

    def test_answering_a_skipped_bare_row_leaves_nothing_set_aside(self, client, conn):
        _txn(conn, "t")
        client.post("/api/annotations/review-queue/skip", json={"transaction_id": "t"})
        client.post("/api/annotations", json={"transaction_id": "t", "category": "Shopping", "confidence": 1.0, "source": "manual"})
        assert _counts(client)["set_aside"] == 0

    def test_skipping_a_missing_payment_is_a_404(self, client):
        assert client.post("/api/annotations/review-queue/skip", json={"transaction_id": "nope"}).status_code == 404

    def test_a_snooze_survives_a_rerun_that_keeps_the_answer_and_clears_when_it_changes(self, conn):
        from finance_copilot.db.queries.annotations import replace_machine_annotation

        _txn(conn, "t", "Shopping", confidence=0.3)
        conn.execute("UPDATE transactions SET review_snoozed_at = datetime('now') WHERE id = 't'")
        args = dict(merchant=None, subcategory=None, tags=None, confidence=0.4, source="llm", reasoning=None)
        replace_machine_annotation(conn, "a_t", category="Shopping", **args)
        assert count_snoozed(conn, 0.85) == 1
        replace_machine_annotation(conn, "a_t", category="Travel", **args)
        assert count_snoozed(conn, 0.85) == 0


class TestCounts:
    def test_counts_split_unsorted_guesses_and_set_aside_without_overlap(self, client, conn):
        _txn(conn, "bare")
        _txn(conn, "uncat", "Uncategorized", confidence=0.9)
        _txn(conn, "guess", "Shopping", confidence=0.3)
        _txn(conn, "sorted", "Shopping", confidence=0.95)
        _txn(conn, "away")
        assert _counts(client) == {"unsorted": 3, "guesses": 1, "set_aside": 0}
        client.post("/api/annotations/review-queue/skip", json={"transaction_id": "away"})
        client.post("/api/annotations/review-queue/skip", json={"transaction_id": "guess"})
        assert _counts(client) == {"unsorted": 2, "guesses": 0, "set_aside": 2}

    def test_an_empty_ledger_counts_zero(self, client):
        assert _counts(client) == {"unsorted": 0, "guesses": 0, "set_aside": 0}

    def test_the_old_snoozed_count_route_is_gone(self, client):
        assert client.get("/api/annotations/review-queue/snoozed-count").status_code == 404


class TestPageCap:
    def test_the_list_is_capped_and_the_counts_carry_the_total(self, client, conn):
        for i in range(230):
            conn.execute(
                "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
                "VALUES (?, 's', '2026-06-01', ?, 'debit', ?)", (f"t{i}", 10 + i, f"UPI/SHOP {i}/12345678{i:04d}/UPI"),
            )
        conn.commit()
        assert len(client.get("/api/annotations/review-queue").json()) == 200
        assert _counts(client)["unsorted"] == 230

    def test_the_cut_never_splits_a_payee_group(self):
        from finance_copilot.pipeline.review_queue import take_whole_groups

        items = [{"group_key": f"g{i}"} for i in range(4)] + [{"group_key": "big"}] * 5 + [{"group_key": "tail"}]
        assert len(take_whole_groups(items, 6)) == 9  # the cut at 6 falls inside "big": it is kept whole
        assert take_whole_groups(items, 4) == items[:4]  # a cut on a boundary stays put
        assert take_whole_groups(items, 50) == items


class TestSnoozeClears:
    def test_a_machine_guess_for_a_skipped_bare_row_brings_it_back(self, client, conn):
        """A skip answers the question that was asked; a later guess is a new one."""
        from finance_copilot.db.queries.annotations import insert_annotation
        from finance_copilot.models.annotation import Annotation

        _txn(conn, "t")
        client.post("/api/annotations/review-queue/skip", json={"transaction_id": "t"})
        assert _queue(client) == {}
        insert_annotation(conn, Annotation(transaction_id="t", category="Shopping", confidence=0.4, source="llm"))
        conn.commit()
        assert set(_queue(client)) == {"t"}
        assert _counts(client)["set_aside"] == 0


class TestAnsweringARaceWithASortingRun:
    def test_a_group_answer_corrects_a_payment_a_run_annotated_in_the_meantime(self, client, conn):
        from finance_copilot.db.queries import annotations as queries

        _txn(conn, "t")
        real = queries.get_annotation_by_transaction
        seen = {"n": 0}

        def stale_first(c, transaction_id):
            seen["n"] += 1
            if seen["n"] == 1:
                # The sorting run lands between this look and the write.
                c.execute("INSERT INTO annotations (id, transaction_id, category, confidence, source) "
                          "VALUES ('a_t', 't', 'Travel', 0.4, 'llm')")
                return None
            return real(c, transaction_id)

        with patch("finance_copilot.api.routes.annotations.get_annotation_by_transaction", stale_first):
            resp = client.post("/api/annotations/bulk-label", json={
                "transaction_ids": ["t"], "label": {"category": "Shopping"},
            })
        assert resp.status_code == 200 and resp.json()["updated"] == 1
        row = conn.execute("SELECT category, source, original_source FROM annotations WHERE transaction_id = 't'").fetchone()
        assert tuple(row) == ("Shopping", "manual", "llm")
        stats = [dict(r) for r in conn.execute("SELECT source, category, corrected FROM feedback_stats")]
        assert stats == [{"source": "llm", "category": "Travel", "corrected": 1}]

    def test_a_hand_label_on_a_bare_row_stores_empty_tags_as_a_json_list(self, client, conn):
        _txn(conn, "t")
        client.post("/api/annotations/bulk-label", json={"transaction_ids": ["t"], "label": {"category": "Shopping"}})
        assert conn.execute("SELECT tags FROM annotations WHERE transaction_id = 't'").fetchone()[0] == "[]"


class TestUncategorizedHasNoSubcategory:
    def test_patching_a_category_to_uncategorized_clears_the_subcategory(self, client, conn):
        _txn(conn, "t", "Food & Dining", confidence=0.4)
        conn.execute("UPDATE annotations SET subcategory = 'Restaurants' WHERE id = 'a_t'")
        conn.commit()
        resp = client.patch("/api/annotations/a_t", json={"category": "Uncategorized"})
        assert resp.status_code == 200
        row = conn.execute("SELECT category, subcategory, subcategory_id FROM annotations WHERE id = 'a_t'").fetchone()
        assert tuple(row) == ("Uncategorized", None, None)


class TestRescore:
    def test_rescore_and_bulk_paths_cope_with_unannotated_payments(self, conn):
        _txn(conn, "bare")
        _txn(conn, "uncat", "Uncategorized", confidence=0.9)
        assert rescore_review_queue(conn) == []

    def test_rescore_gives_a_bare_row_the_label_a_rule_now_settles(self, conn):
        from finance_copilot.db.queries.user_rules import create_user_rule

        _txn(conn, "bare")
        create_user_rule(conn, "SHOP", "Shopping")
        conn.commit()
        changed = rescore_review_queue(conn)
        assert [(c["transaction_id"], c["category"], c["source"], c["resolved"]) for c in changed] == [
            ("bare", "Shopping", "user_rule", True)
        ]
        assert conn.execute("SELECT source FROM annotations WHERE transaction_id = 'bare'").fetchone()[0] == "user_rule"
        assert list_review_queue(conn, 0.85) == []

    def test_rescore_leaves_a_bare_row_nothing_decides_without_a_label(self, conn):
        _txn(conn, "bare")
        assert rescore_review_queue(conn) == []
        assert conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 0


class TestPoliciesSeeBareRows:
    def _stranger(self, conn, txn_id):
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
            "VALUES (?, 's', '2026-06-02', 40, 'debit', 'UPI/ROHIT YADAV/123456789012/UPI')", (txn_id,),
        )
        conn.commit()

    def test_preview_and_apply_include_payments_with_no_annotation(self, client, conn):
        self._stranger(conn, "bare")
        body = {"max_amount": 500, "category": "Transport", "subcategory": "Cab & Auto"}
        assert client.post("/api/policies/preview", json=body).json() == {"matches": 1}
        created = client.post("/api/policies", json=body).json()
        assert created["resolved"] == 1
        row = conn.execute("SELECT category, source FROM annotations WHERE transaction_id = 'bare'").fetchone()
        assert tuple(row) == ("Transport", "policy")
        assert list_review_queue(conn, 0.85) == []


class TestSnoozeMigration:
    def _old_ledger(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT (datetime('now')))")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name >= "035":
                continue
            for stmt in _iter_statements(path.read_text()):
                if "vec_items" in stmt:
                    continue
                c.execute(stmt)
            c.execute("INSERT INTO schema_migrations (version) VALUES (?)", (path.name,))
        c.execute("INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s', 't', '1', '2026-06')")
        for tid in ("snoozed", "plain", "bare"):
            c.execute(
                "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
                "VALUES (?, 's', '2026-06-01', 10, 'debit', 'X')", (tid,),
            )
        c.execute("INSERT INTO annotations (id, transaction_id, category, confidence, source, review_snoozed_at) "
                  "VALUES ('a1', 'snoozed', 'Shopping', 0.3, 'llm', '2026-06-05 10:00:00')")
        c.execute("INSERT INTO annotations (id, transaction_id, category, confidence, source) "
                  "VALUES ('a2', 'plain', 'Shopping', 0.3, 'llm')")
        c.commit()
        return c

    def test_snoozed_rows_move_to_the_transaction(self):
        c = self._old_ledger()
        init_db(c)
        rows = {r["id"]: r["review_snoozed_at"] for r in c.execute("SELECT id, review_snoozed_at FROM transactions")}
        assert rows == {"snoozed": "2026-06-05 10:00:00", "plain": None, "bare": None}
        assert "review_snoozed_at" not in {r["name"] for r in c.execute("PRAGMA table_info(annotations)")}
        indexes = {r["name"] for r in c.execute("PRAGMA index_list(transactions)")}
        assert "idx_transactions_review_snoozed" in indexes
        assert {r["name"] for r in c.execute("PRAGMA index_list(annotations)")}.isdisjoint({"idx_annotations_review_snoozed"})

    def test_a_second_run_changes_nothing(self):
        c = self._old_ledger()
        init_db(c)
        before = [tuple(r) for r in c.execute("SELECT id, review_snoozed_at FROM transactions ORDER BY id")]
        init_db(c)
        assert [tuple(r) for r in c.execute("SELECT id, review_snoozed_at FROM transactions ORDER BY id")] == before
        assert count_snoozed(c, 0.85) == 1
