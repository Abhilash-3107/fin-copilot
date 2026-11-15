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

    @pytest.mark.parametrize("source", ["manual", "imported", "user_rule"])
    def test_a_human_label_is_never_queued_even_as_uncategorized(self, client, conn, source):
        _txn(conn, "t", "Uncategorized", source=source, confidence=1.0)
        assert _queue(client) == {}

    @pytest.mark.parametrize("source", ["llm", "rag_direct", "learned_rule", "policy", "model"])
    def test_a_machine_uncategorized_is_queued_at_any_confidence(self, client, conn, source):
        _txn(conn, "t", "Uncategorized", source=source, confidence=0.99)
        assert set(_queue(client)) == {"t"}

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
        unsorted = {
            r[0] for r in conn.execute(
                f"SELECT t.id FROM transactions t LEFT JOIN annotations a ON a.transaction_id = t.id WHERE {unsorted_sql('a')}"
            )
        }
        assert unsorted == {"a", "b"}
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
        assert client.get("/api/annotations/review-queue/snoozed-count").json() == {"count": 1}

    def test_unsnooze_restores_both_kinds(self, client, conn):
        _txn(conn, "bare")
        _txn(conn, "low", "Shopping", confidence=0.3)
        for t in ("bare", "low"):
            client.post("/api/annotations/review-queue/skip", json={"transaction_id": t})
        assert _queue(client) == {}
        assert client.post("/api/annotations/review-queue/unsnooze").json() == {"restored": 2}
        assert set(_queue(client)) == {"bare", "low"}
        assert client.get("/api/annotations/review-queue/snoozed-count").json() == {"count": 0}

    def test_snoozed_count_is_right_after_skip_back_answer(self, client, conn):
        """AUDIT B2: skip, go back, answer. Nothing is left set aside."""
        _txn(conn, "t", "Uncategorized", confidence=0.9)
        client.post("/api/annotations/review-queue/skip", json={"transaction_id": "t"})
        client.patch("/api/annotations/a_t", json={"category": "Shopping"})
        assert client.get("/api/annotations/review-queue/snoozed-count").json() == {"count": 0}
        assert client.post("/api/annotations/review-queue/unsnooze").json() == {"restored": 0}

    def test_answering_a_skipped_bare_row_leaves_nothing_set_aside(self, client, conn):
        _txn(conn, "t")
        client.post("/api/annotations/review-queue/skip", json={"transaction_id": "t"})
        client.post("/api/annotations", json={"transaction_id": "t", "category": "Shopping", "confidence": 1.0, "source": "manual"})
        assert client.get("/api/annotations/review-queue/snoozed-count").json() == {"count": 0}

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


class TestRescore:
    def test_rescore_and_bulk_paths_cope_with_unannotated_payments(self, conn):
        _txn(conn, "bare")
        _txn(conn, "uncat", "Uncategorized", confidence=0.9)
        assert rescore_review_queue(conn) == []


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
