"""API route tests: annotation PATCH/confirm semantics, feedback recording, upload dedup."""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.annotations import get_annotation_by_transaction, insert_annotation
from finance_copilot.models.annotation import Annotation


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def _seed_annotated_txn(conn, txn_id="t1", ann_id="a1", source="llm", **ann_fields):
    conn.execute(
        "INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','test','1','2026-01')"
    )
    conn.execute(
        """INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description)
           VALUES (?, 's1', '2026-01-15', 100.0, 'debit', 'SOME MERCHANT')""",
        (txn_id,),
    )
    defaults = dict(
        transaction_id=txn_id, category="Shopping", subcategory="Online Shopping",
        merchant="SomeShop", tags="shopping", confidence=0.7, source=source,
    )
    defaults.update(ann_fields)
    ann = Annotation(id=ann_id, **defaults)
    insert_annotation(conn, ann)
    conn.commit()
    return ann


@pytest.fixture
def client_conn():
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.main import app

    conn = _make_conn()
    app.dependency_overrides[api_get_db] = lambda: conn
    # Embedding service isn't available in tests — stub the best-effort embed call.
    # No context manager on TestClient: lifespan would migrate the real settings.db_path.
    # Background re-embeds open their own connection to settings.db_path (the
    # real ledger in a dev checkout) - never let a test reach it.
    with patch("finance_copilot.api.routes.annotations.embed_transaction", return_value=False) as embed_mock, \
            patch("finance_copilot.api.routes.annotations._embed_in_background"):
        client = TestClient(app)
        yield client, conn, embed_mock
    app.dependency_overrides.pop(api_get_db, None)
    conn.close()


class TestHealthLocalEmbeddings:
    """Pythons without onnxruntime wheels install without fastembed; health says so."""

    @pytest.mark.parametrize("installed", [True, False])
    def test_reports_whether_builtin_embeddings_can_run(self, client_conn, installed):
        client, _, _ = client_conn
        with patch(
            "finance_copilot.pipeline.embed.local_embeddings_installed", return_value=installed
        ):
            assert client.get("/api/health").json()["local_embeddings_available"] is installed

    def test_local_provider_without_fastembed_is_unavailable(self, client_conn, monkeypatch):
        from finance_copilot.config import settings
        client, _, _ = client_conn
        monkeypatch.setattr(settings, "embedding_provider", "local")
        with patch(
            "finance_copilot.pipeline.embed.local_embeddings_installed", return_value=False
        ):
            assert client.get("/api/health").json()["embeddings"] == "unavailable"


class TestApiTokenGate:
    """4.9: when settings.api_token is set, /api requests (except health) need it."""

    def test_no_token_configured_allows_everything(self, client_conn):
        client, conn, _ = client_conn
        assert client.get("/api/categories").status_code == 200
        assert client.get("/api/health").json()["auth_required"] is False

    def test_token_required_when_configured(self, client_conn, monkeypatch):
        from finance_copilot.config import settings
        client, conn, _ = client_conn
        monkeypatch.setattr(settings, "api_token", "s3cret")

        # Health is always reachable and advertises that auth is on.
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["auth_required"] is True

        # A protected route is blocked without the token, allowed with it.
        assert client.get("/api/categories").status_code == 401
        assert client.get("/api/categories", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = client.get("/api/categories", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
        # X-API-Token header works too.
        assert client.get("/api/categories", headers={"X-API-Token": "s3cret"}).status_code == 200

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_api_docs_gated_when_token_set(self, client_conn, monkeypatch, path):
        # Review M3: the schema routes live outside /api/ and bypassed the gate.
        from finance_copilot.config import settings
        client, _, _ = client_conn
        assert client.get(path).status_code == 200
        monkeypatch.setattr(settings, "api_token", "s3cret")
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"X-API-Token": "s3cret"}).status_code == 200

    def test_token_compared_in_constant_time(self, client_conn, monkeypatch):
        from finance_copilot.config import settings
        client, _, _ = client_conn
        monkeypatch.setattr(settings, "api_token", "s3cret")
        with patch("finance_copilot.main.secrets.compare_digest", wraps=__import__("secrets").compare_digest) as cmp:
            assert client.get("/api/categories", headers={"X-API-Token": "s3cret"}).status_code == 200
        cmp.assert_called_once()
        # Non-ASCII junk must be a clean 401, not a 500 from compare_digest.
        assert client.get("/api/categories", headers={"Authorization": b"Bearer t\xc3\xb6ken"}).status_code == 401


class TestAnnotationPatch:
    def test_patch_updates_field(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn)
        resp = client.patch("/api/annotations/a1", json={"category": "Entertainment"})
        assert resp.status_code == 200
        assert resp.json()["category"] == "Entertainment"

    def test_patch_explicit_null_clears_nullable_field(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn)
        resp = client.patch("/api/annotations/a1", json={"subcategory": None, "merchant": None})
        assert resp.status_code == 200
        body = resp.json()
        assert body["subcategory"] is None
        assert body["merchant"] is None

    def test_patch_omitted_fields_untouched(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn)
        resp = client.patch("/api/annotations/a1", json={"category": "Entertainment"})
        body = resp.json()
        assert body["subcategory"] == "Online Shopping"
        assert body["merchant"] == "SomeShop"

    def test_patch_null_category_rejected(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn)
        resp = client.patch("/api/annotations/a1", json={"category": None})
        assert resp.status_code == 422

    def test_patch_preserves_original_source(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, source="rag_direct")
        resp = client.patch("/api/annotations/a1", json={"category": "Entertainment"})
        body = resp.json()
        assert body["source"] == "manual"
        assert body["original_source"] == "rag_direct"

    def test_second_patch_keeps_first_original_source(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, source="llm")
        client.patch("/api/annotations/a1", json={"category": "Entertainment"})
        resp = client.patch("/api/annotations/a1", json={"category": "Travel"})
        assert resp.json()["original_source"] == "llm"

    def test_patch_404(self, client_conn):
        client, _, _ = client_conn
        resp = client.patch("/api/annotations/nope", json={"category": "X"})
        assert resp.status_code == 404

    def test_patch_triggers_embedding(self, client_conn):
        client, conn, embed_mock = client_conn
        _seed_annotated_txn(conn)
        client.patch("/api/annotations/a1", json={"category": "Entertainment"})
        embed_mock.assert_called_once_with(conn, "t1")


class TestFeedbackRecording:
    def _feedback_rows(self, conn):
        return {
            (r["source"], r["category"]): dict(r)
            for r in conn.execute("SELECT * FROM feedback_stats").fetchall()
        }

    @pytest.mark.parametrize("source", ["llm", "rag_prompted", "rag_direct", "rule"])
    def test_correction_recorded_for_all_model_sources(self, client_conn, source):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, source=source)
        client.patch("/api/annotations/a1", json={"category": "Entertainment"})
        rows = self._feedback_rows(conn)
        assert rows[(source, "Shopping")]["corrected"] == 1

    def test_confirm_recorded_for_rag_direct(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, source="rag_direct")
        resp = client.post("/api/annotations/a1/confirm", json={"category": "Shopping"})
        assert resp.status_code == 200
        rows = self._feedback_rows(conn)
        assert rows[("rag_direct", "Shopping")]["confirmed"] == 1

    def test_manual_source_records_no_feedback(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, source="manual")
        client.patch("/api/annotations/a1", json={"category": "Entertainment"})
        assert self._feedback_rows(conn) == {}


class TestJsonTags:
    def test_tags_with_commas_round_trip(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn)
        resp = client.patch("/api/annotations/a1", json={"tags": ["food, delivery", "weekend"]})
        assert resp.json()["tags"] == ["food, delivery", "weekend"]
        # And through the list endpoint too
        rows = client.get("/api/transactions?include=annotation").json()
        assert rows[0]["tags"] == ["food, delivery", "weekend"]

    def test_legacy_comma_string_still_parses(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, tags="food,delivery")  # pre-migration format
        rows = client.get("/api/transactions?include=annotation").json()
        assert rows[0]["tags"] == ["food", "delivery"]

    def test_clearing_tags_stores_empty_array(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn)
        resp = client.patch("/api/annotations/a1", json={"tags": []})
        assert resp.json()["tags"] == []
        raw = conn.execute("SELECT tags FROM annotations WHERE id='a1'").fetchone()[0]
        assert raw == "[]"


class TestCategoryIds:
    def test_insert_resolves_ids(self, client_conn):
        _, conn, _ = client_conn
        _seed_annotated_txn(conn, category="Shopping", subcategory="Online Shopping")
        row = conn.execute("SELECT category_id, subcategory_id FROM annotations WHERE id='a1'").fetchone()
        assert row["category_id"] == "cat_shopping"
        assert row["subcategory_id"] == "cat_shop_online"

    def test_insert_with_free_text_subcategory_leaves_id_null(self, client_conn):
        _, conn, _ = client_conn
        _seed_annotated_txn(conn, category="Shopping", subcategory="LLM Made This Up")
        row = conn.execute("SELECT category_id, subcategory_id FROM annotations WHERE id='a1'").fetchone()
        assert row["category_id"] == "cat_shopping"
        assert row["subcategory_id"] is None

    def test_patch_updates_ids(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn)
        client.patch("/api/annotations/a1", json={"category": "Entertainment", "subcategory": "Movies & OTT"})
        row = conn.execute("SELECT category_id, subcategory_id FROM annotations WHERE id='a1'").fetchone()
        assert row["category_id"] == "cat_entertainment"
        assert row["subcategory_id"] == "cat_ent_movies"

    def test_patch_unknown_category_rejected(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn)
        resp = client.patch("/api/annotations/a1", json={"category": "Subscriptions"})
        assert resp.status_code == 422

    def test_patch_subcategory_must_belong_to_category(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn)
        resp = client.patch(
            "/api/annotations/a1",
            json={"category": "Entertainment", "subcategory": "Groceries"},
        )
        assert resp.status_code == 422

    def test_create_unknown_category_rejected(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, txn_id="t9", ann_id="a9")
        conn.execute(
            """INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description)
               VALUES ('t_new', 's1', '2026-01-16', 50.0, 'debit', 'X')"""
        )
        conn.commit()
        resp = client.post("/api/annotations", json={
            "transaction_id": "t_new", "category": "Nope", "source": "manual",
        })
        assert resp.status_code == 422

    def test_create_on_already_annotated_txn_conflicts(self, client_conn):
        # POST must not silently overwrite an existing annotation (4.5); it 409s
        # so the caller PATCHes instead of losing original_source/reasoning.
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, txn_id="t1", ann_id="a1", source="llm")
        resp = client.post("/api/annotations", json={
            "transaction_id": "t1", "category": "Shopping", "source": "manual",
        })
        assert resp.status_code == 409


class TestConfirmFlow:
    def test_confirm_sets_confidence_and_provenance(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, source="llm", confidence=0.6)
        resp = client.post("/api/annotations/a1/confirm", json={"category": "Shopping"})
        body = resp.json()
        assert body["confidence"] == 1.0
        assert body["source"] == "manual"
        assert body["original_source"] == "llm"

    def test_confirm_refuses_a_label_the_user_did_not_see(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, source="llm", confidence=0.6)
        resp = client.post("/api/annotations/a1/confirm", json={"category": "Travel"})
        assert resp.status_code == 409
        assert conn.execute("SELECT source FROM annotations WHERE id = 'a1'").fetchone()[0] == "llm"


class TestReviewSnooze:
    """4.15: skipping snoozes an item out of the queue persistently, with a
    surfaced count and a restore-all path."""

    def test_skip_removes_from_queue_and_counts(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, txn_id="t1", ann_id="a1", source="llm", confidence=0.6)

        assert any(i["annotation_id"] == "a1" for i in client.get("/api/annotations/review-queue").json())

        resp = client.post("/api/annotations/a1/skip")
        assert resp.status_code == 200

        # Gone from the queue, counted as snoozed.
        assert all(i["annotation_id"] != "a1" for i in client.get("/api/annotations/review-queue").json())
        assert client.get("/api/annotations/review-queue/snoozed-count").json()["count"] == 1

    def test_unsnooze_restores(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, txn_id="t1", ann_id="a1", source="llm", confidence=0.6)
        client.post("/api/annotations/a1/skip")

        resp = client.post("/api/annotations/review-queue/unsnooze")
        assert resp.json()["restored"] == 1
        assert any(i["annotation_id"] == "a1" for i in client.get("/api/annotations/review-queue").json())
        assert client.get("/api/annotations/review-queue/snoozed-count").json()["count"] == 0

    def test_skip_unknown_annotation_404s(self, client_conn):
        client, _, _ = client_conn
        assert client.post("/api/annotations/nope/skip").status_code == 404

    def test_correction_clears_snooze(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, txn_id="t1", ann_id="a1", source="llm", confidence=0.6)
        client.post("/api/annotations/a1/skip")
        # Correcting the annotation should drop the lingering snooze flag.
        client.patch("/api/annotations/a1", json={"category": "Food & Dining"})
        assert client.get("/api/annotations/review-queue/snoozed-count").json()["count"] == 0


class TestTransactionsList:
    def _seed_many(self, conn, n=5):
        conn.execute(
            "INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','test','1','2026-01')"
        )
        for i in range(n):
            conn.execute(
                """INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description)
                   VALUES (?, 's1', ?, 50.0, 'debit', ?)""",
                (f"t{i}", f"2026-01-{10 + i:02d}", f"TXN {i}"),
            )
        conn.commit()

    def test_include_annotation_single_request(self, client_conn):
        client, conn, _ = client_conn
        self._seed_many(conn, 2)
        insert_annotation(conn, Annotation(
            id="a0", transaction_id="t0", category="Shopping", confidence=0.9, source="rule",
        ))
        conn.commit()

        rows = client.get("/api/transactions?include=annotation").json()
        by_id = {r["id"]: r for r in rows}
        assert by_id["t0"]["annotation_id"] == "a0"
        assert by_id["t0"]["category"] == "Shopping"
        assert by_id["t1"]["annotation_id"] is None

    def test_plain_list_has_no_annotation_columns(self, client_conn):
        client, conn, _ = client_conn
        self._seed_many(conn, 1)
        rows = client.get("/api/transactions").json()
        assert "annotation_id" not in rows[0]

    def test_cursor_pagination(self, client_conn):
        client, conn, _ = client_conn
        self._seed_many(conn, 5)
        # Default sort is newest-first (date desc), tie-broken by id.
        page1 = client.get("/api/transactions?limit=2").json()
        assert [r["id"] for r in page1] == ["t4", "t3"]
        page2 = client.get(f"/api/transactions?limit=2&after={page1[-1]['id']}").json()
        assert [r["id"] for r in page2] == ["t2", "t1"]
        page3 = client.get(f"/api/transactions?limit=2&after={page2[-1]['id']}").json()
        assert [r["id"] for r in page3] == ["t0"]

    def test_ascending_sort_by_date(self, client_conn):
        client, conn, _ = client_conn
        self._seed_many(conn, 3)
        rows = client.get("/api/transactions?order=asc").json()
        assert [r["id"] for r in rows] == ["t0", "t1", "t2"]

    def test_unknown_cursor_ignored(self, client_conn):
        client, conn, _ = client_conn
        self._seed_many(conn, 2)
        rows = client.get("/api/transactions?after=missing&limit=10").json()
        assert len(rows) == 2

    def test_amount_filter_and_amount_sort(self, client_conn):
        client, conn, _ = client_conn
        conn.execute("INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','t','1','2026-01')")
        for i, amt in enumerate([10.0, 500.0, 2000.0]):
            conn.execute(
                "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
                "VALUES (?, 's1', '2026-01-10', ?, 'debit', ?)",
                (f"a{i}", amt, f"row {i}"),
            )
        conn.commit()
        # min/max window keeps only the middle row.
        mid = client.get("/api/transactions?min_amount=100&max_amount=1000").json()
        assert [r["id"] for r in mid] == ["a1"]
        # Sort by amount ascending.
        asc = client.get("/api/transactions?sort=amount&order=asc").json()
        assert [r["amount"] for r in asc] == [10.0, 500.0, 2000.0]

    def test_bulk_set_category(self, client_conn):
        client, conn, _ = client_conn
        conn.execute("INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','t','1','2026-01')")
        for i in range(3):
            conn.execute(
                "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
                "VALUES (?, 's1', '2026-01-10', 10.0, 'debit', ?)",
                (f"b{i}", f"row {i}"),
            )
        conn.commit()
        # One already annotated by a model; two unannotated.
        insert_annotation(conn, Annotation(id="pre", transaction_id="b0", category="Shopping", confidence=0.4, source="llm"))
        conn.commit()

        resp = client.post("/api/annotations/bulk-category", json={
            "transaction_ids": ["b0", "b1", "b2"], "category": "Food & Dining", "subcategory": "Restaurants",
        })
        assert resp.json()["updated"] == 3
        rows = client.get("/api/transactions?include=annotation").json()
        by_id = {r["id"]: r for r in rows}
        for tid in ("b0", "b1", "b2"):
            assert by_id[tid]["category"] == "Food & Dining"
            assert by_id[tid]["subcategory"] == "Restaurants"
            assert by_id[tid]["source"] == "manual"
        # The pre-existing model label's history is preserved.
        assert by_id["b0"]["original_source"] == "llm"

    def test_bulk_skips_ids_that_no_longer_exist(self, client_conn):
        # Review M1: a transaction deleted since the page loaded used to trip the
        # annotations FK and 500 the whole batch.
        client, conn, _ = client_conn
        conn.execute("INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','t','1','2026-01')")
        conn.execute("INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) VALUES ('b0','s1','2026-01-10',10.0,'debit','x')")
        conn.commit()
        resp = client.post("/api/annotations/bulk-category", json={
            "transaction_ids": ["b0", "gone"], "category": "Food & Dining",
        })
        assert resp.status_code == 200
        assert resp.json() == {"updated": 1, "skipped": ["gone"]}
        assert get_annotation_by_transaction(conn, "b0")["category"] == "Food & Dining"

    def test_bulk_all_unknown_ids(self, client_conn):
        client, _, _ = client_conn
        resp = client.post("/api/annotations/bulk-category", json={
            "transaction_ids": ["x", "y"], "category": "Food & Dining",
        })
        assert resp.json() == {"updated": 0, "skipped": ["x", "y"]}

    def test_bulk_reembeds_after_responding(self, client_conn):
        # Review M2: re-embedding ran inline, one provider call per row.
        client, conn, embed_mock = client_conn
        conn.execute("INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','t','1','2026-01')")
        for i in range(3):
            conn.execute(
                "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
                "VALUES (?, 's1', '2026-01-10', 10.0, 'debit', 'x')", (f"b{i}",))
        conn.commit()
        with patch("finance_copilot.api.routes.annotations._embed_in_background") as bg:
            client.post("/api/annotations/bulk-category", json={
                "transaction_ids": ["b0", "b1", "b2", "gone"], "category": "Food & Dining",
            })
        embed_mock.assert_not_called()
        bg.assert_called_once_with(["b0", "b1", "b2"])

    def test_bulk_unknown_category_rejected(self, client_conn):
        client, conn, _ = client_conn
        conn.execute("INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','t','1','2026-01')")
        conn.execute("INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) VALUES ('b0','s1','2026-01-10',10.0,'debit','x')")
        conn.commit()
        resp = client.post("/api/annotations/bulk-category", json={"transaction_ids": ["b0"], "category": "Nope"})
        assert resp.status_code == 422


class TestTransactionsFilters:
    def _seed(self, conn):
        conn.execute(
            "INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','test','1','2026-01')"
        )
        rows = [
            ("t_food", "UPI-SWIGGY-ORDER", '{"note": "friday biryani"}'),
            ("t_shop", "UPI-AMAZN-PAY", None),
            ("t_bare", "NEFT SALARY CREDIT", None),
        ]
        for txn_id, desc, upi_meta in rows:
            conn.execute(
                """INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, upi_meta)
                   VALUES (?, 's1', '2026-01-15', 100.0, 'debit', ?, ?)""",
                (txn_id, desc, upi_meta),
            )
        insert_annotation(conn, Annotation(
            id="a_food", transaction_id="t_food", category="Food & Dining",
            merchant="Swiggy", confidence=0.9, source="rule",
        ))
        insert_annotation(conn, Annotation(
            id="a_shop", transaction_id="t_shop", category="Shopping",
            merchant="Amazon", confidence=0.7, source="llm",
        ))
        conn.commit()

    def _ids(self, client, query):
        return {r["id"] for r in client.get(f"/api/transactions?{query}").json()}

    def test_q_matches_description(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)
        assert self._ids(client, "q=swiggy") == {"t_food"}

    def test_q_matches_annotated_merchant(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)
        assert self._ids(client, "q=amazon") == {"t_shop"}

    def test_q_matches_upi_note(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)
        assert self._ids(client, "q=biryani") == {"t_food"}

    def test_q_like_wildcards_are_literal(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)
        assert self._ids(client, "q=%25") == set()  # '%' matches nothing literally

    def test_q_matches_amount(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)  # all rows are 100.0
        assert self._ids(client, "q=100") == {"t_food", "t_shop", "t_bare"}

    def test_q_amount_tolerates_rupee_and_commas(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)
        assert self._ids(client, "q=%E2%82%B9100") == {"t_food", "t_shop", "t_bare"}

    def test_q_whole_rupees_match_the_rupee_part(self, client_conn):
        # Review L2: "1200" should find ₹1,200.50, as the comment always promised;
        # a query with paise stays exact.
        client, conn, _ = client_conn
        self._seed(conn)
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
            "VALUES ('t_paise', 's1', '2026-01-15', 1200.5, 'debit', 'X'), "
            "('t_round', 's1', '2026-01-15', 1200.0, 'debit', 'Y'), "
            "('t_next', 's1', '2026-01-15', 1201.0, 'debit', 'Z')"
        )
        conn.commit()
        assert self._ids(client, "q=1200") == {"t_paise", "t_round"}
        assert self._ids(client, "q=1,200") == {"t_paise", "t_round"}
        assert self._ids(client, "q=1200.50") == {"t_paise"}
        assert self._ids(client, "q=1200.00") == {"t_round"}

    def test_q_non_matching_amount_empty(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)
        assert self._ids(client, "q=999") == set()

    def test_category_filter_multi(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)
        got = self._ids(client, "category=Food%20%26%20Dining,Shopping")
        assert got == {"t_food", "t_shop"}
        assert self._ids(client, "category=Shopping") == {"t_shop"}

    def test_source_filter(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)
        assert self._ids(client, "source=llm") == {"t_shop"}

    def test_merchant_filter_exact(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)
        assert self._ids(client, "merchant=Swiggy") == {"t_food"}

    def test_filters_compose_with_pagination(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)
        page1 = client.get("/api/transactions?category=Food%20%26%20Dining,Shopping&limit=1").json()
        assert len(page1) == 1
        page2 = client.get(
            f"/api/transactions?category=Food%20%26%20Dining,Shopping&limit=1&after={page1[0]['id']}"
        ).json()
        assert len(page2) == 1
        assert {page1[0]["id"], page2[0]["id"]} == {"t_food", "t_shop"}

    def test_facets_lists_scope_categories_and_sources(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)
        facets = client.get("/api/transactions/facets").json()
        assert facets["categories"] == ["Food & Dining", "Shopping"]
        assert facets["sources"] == ["llm", "rule"]

    def test_facets_respect_month_scope(self, client_conn):
        client, conn, _ = client_conn
        self._seed(conn)
        facets = client.get("/api/transactions/facets?month=2025-12").json()
        assert facets == {"categories": [], "sources": []}


class TestCategoryAccuracy:
    """4.19: per-category accuracy from human feedback, weakest first + sample size."""

    def _feedback(self, conn, category, confirmed, corrected, source="llm"):
        conn.execute(
            "INSERT INTO feedback_stats (source, category, confirmed, refined, corrected) VALUES (?, ?, ?, 0, ?)",
            (source, category, confirmed, corrected),
        )
        conn.commit()

    def test_ranks_weakest_first_with_sample_size(self, client_conn):
        client, conn, _ = client_conn
        self._feedback(conn, "Shopping", confirmed=9, corrected=1)   # 90%
        self._feedback(conn, "Miscellaneous", confirmed=2, corrected=6)  # 25%
        self._feedback(conn, "Food & Dining", confirmed=5, corrected=0)  # 100%
        rows = client.get("/api/annotations/accuracy").json()
        assert [r["category"] for r in rows] == ["Miscellaneous", "Shopping", "Food & Dining"]
        misc = rows[0]
        assert misc["reviewed"] == 8 and misc["changed"] == 6
        assert abs(misc["accuracy"] - 0.25) < 1e-6

    def test_below_min_reviews_excluded(self, client_conn):
        client, conn, _ = client_conn
        self._feedback(conn, "Travel", confirmed=1, corrected=1)  # only 2 reviews < 3
        rows = client.get("/api/annotations/accuracy").json()
        assert all(r["category"] != "Travel" for r in rows)


class TestCategoryManagement:
    """4.13: add / rename / merge subcategories, keeping annotation names in sync."""

    def _parent(self, client):
        cats = client.get("/api/categories").json()
        return next(c for c in cats if c["name"] == "Food & Dining" and c["parent_id"] is None)

    def test_add_subcategory(self, client_conn):
        client, conn, _ = client_conn
        parent = self._parent(client)
        resp = client.post("/api/categories/subcategories", json={"parent_id": parent["id"], "name": "Street Food"})
        assert resp.status_code == 201
        cats = client.get("/api/categories").json()
        assert any(c["name"] == "Street Food" and c["parent_id"] == parent["id"] for c in cats)

    def test_add_duplicate_rejected(self, client_conn):
        client, conn, _ = client_conn
        parent = self._parent(client)
        client.post("/api/categories/subcategories", json={"parent_id": parent["id"], "name": "Zzz"})
        dup = client.post("/api/categories/subcategories", json={"parent_id": parent["id"], "name": "zzz"})
        assert dup.status_code == 422

    def test_rename_cascades_to_annotations(self, client_conn):
        client, conn, _ = client_conn
        parent = self._parent(client)
        sub_id = client.post(
            "/api/categories/subcategories", json={"parent_id": parent["id"], "name": "Takeout"}
        ).json()["id"]
        # Annotate a txn with the new subcategory (resolves subcategory_id).
        conn.execute("INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','t','1','2026-01')")
        conn.execute("INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
                     "VALUES ('tx','s1','2026-01-10',10.0,'debit','x')")
        conn.commit()
        client.post("/api/annotations", json={
            "transaction_id": "tx", "category": "Food & Dining", "subcategory": "Takeout", "source": "manual",
        })
        client.patch(f"/api/categories/subcategories/{sub_id}", json={"name": "Takeaway"})
        row = conn.execute("SELECT subcategory FROM annotations WHERE transaction_id='tx'").fetchone()
        assert row["subcategory"] == "Takeaway"

    def test_merge_repoints_annotations(self, client_conn):
        client, conn, _ = client_conn
        parent = self._parent(client)
        a = client.post("/api/categories/subcategories", json={"parent_id": parent["id"], "name": "SrcSub"}).json()["id"]
        b = client.post("/api/categories/subcategories", json={"parent_id": parent["id"], "name": "TgtSub"}).json()["id"]
        conn.execute("INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','t','1','2026-01')")
        conn.execute("INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
                     "VALUES ('tx','s1','2026-01-10',10.0,'debit','x')")
        conn.commit()
        client.post("/api/annotations", json={
            "transaction_id": "tx", "category": "Food & Dining", "subcategory": "SrcSub", "source": "manual",
        })
        resp = client.post(f"/api/categories/subcategories/{a}/merge", json={"target_id": b})
        assert resp.json()["moved"] == 1
        # Source gone, annotation now points at target.
        cats = client.get("/api/categories").json()
        assert all(c["id"] != a for c in cats)
        row = conn.execute("SELECT subcategory, subcategory_id FROM annotations WHERE transaction_id='tx'").fetchone()
        assert row["subcategory"] == "TgtSub" and row["subcategory_id"] == b


class TestDataManagement:
    """4.14: delete-everything wipes user data but keeps the seeded categories."""

    def test_delete_all_wipes_user_data_keeps_categories(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, txn_id="t1", ann_id="a1")
        conn.execute("INSERT INTO people (id, name, upi) VALUES ('p1','x','x@u')")
        conn.commit()
        cats_before = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        assert cats_before > 0

        resp = client.post("/api/data/delete-all")
        assert resp.status_code == 200

        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 0
        # The taxonomy survives so the app still has categories to assign.
        assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == cats_before


class TestStatementBalanceCheck:
    """4.20: the statements list reports per-statement running-balance
    reconciliation so the parser's rigor becomes a visible trust signal."""

    def _seed_chain(self, conn, stmt_id, rows):
        # rows: list of (amount, debit_credit, running_balance)
        conn.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES (?, 'test', '1', '2026-01')",
            (stmt_id,),
        )
        for i, (amt, dc, bal) in enumerate(rows):
            conn.execute(
                """INSERT INTO transactions
                   (id, statement_id, txn_date, amount, debit_credit, raw_description, running_balance)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (f"{stmt_id}_{i}", stmt_id, f"2026-01-{10 + i:02d}", amt, dc, f"row {i}", bal),
            )
        conn.commit()

    def _check_for(self, client, stmt_id):
        body = client.get("/api/statements").json()
        return next(s["balance_check"] for s in body if s["id"] == stmt_id)

    def test_consistent_chain_is_balanced(self, client_conn):
        client, conn, _ = client_conn
        # 1000 → -200 debit → 800 → +50 credit → 850. Two consecutive checks, both OK.
        self._seed_chain(conn, "s_ok", [(0.0, "credit", 1000.0), (200.0, "debit", 800.0), (50.0, "credit", 850.0)])
        chk = self._check_for(client, "s_ok")
        assert chk["checked"] == 2
        assert chk["consistent"] == 2
        assert chk["balanced"] is True

    def test_broken_chain_flagged(self, client_conn):
        client, conn, _ = client_conn
        # Second step claims 800 but 1000 - 200 credit would be 1200; mismatch.
        self._seed_chain(conn, "s_bad", [(0.0, "credit", 1000.0), (200.0, "credit", 800.0)])
        chk = self._check_for(client, "s_bad")
        assert chk["checked"] == 1
        assert chk["consistent"] == 0
        assert chk["balanced"] is False

    def test_no_balances_reports_nothing_to_check(self, client_conn):
        client, conn, _ = client_conn
        conn.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s_nb','t','1','2026-01')"
        )
        conn.execute(
            """INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description)
               VALUES ('s_nb_0','s_nb','2026-01-10', 10.0, 'debit', 'x')"""
        )
        conn.commit()
        chk = self._check_for(client, "s_nb")
        assert chk["checked"] == 0
        assert chk["balanced"] is True


class TestStatementDeleteCascade:
    def _seed_statement_with_data(self, conn, stmt_id="s_del"):
        conn.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES (?, 'test', '1', '2026-01')",
            (stmt_id,),
        )
        conn.execute(
            """INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description)
               VALUES (?, ?, '2026-01-10', 10.0, 'debit', 'D')""",
            (f"{stmt_id}_t", stmt_id),
        )
        insert_annotation(conn, Annotation(
            id=f"{stmt_id}_a", transaction_id=f"{stmt_id}_t",
            category="Shopping", confidence=0.9, source="rule",
        ))
        conn.execute(
            "INSERT INTO embedding_meta (id, transaction_id, model_version) VALUES (?, ?, 'm')",
            (f"{stmt_id}_e", f"{stmt_id}_t"),
        )
        conn.commit()

    def test_delete_removes_transactions_annotations_embeddings(self, client_conn):
        client, conn, _ = client_conn
        self._seed_statement_with_data(conn)
        # An unrelated statement must survive
        self._seed_statement_with_data(conn, stmt_id="s_keep")

        resp = client.delete("/api/statements/s_del")
        assert resp.status_code == 200

        def count(table, col, val):
            return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (val,)).fetchone()[0]

        assert count("statements", "id", "s_del") == 0
        assert count("transactions", "statement_id", "s_del") == 0
        assert count("annotations", "transaction_id", "s_del_t") == 0
        assert count("embedding_meta", "transaction_id", "s_del_t") == 0
        # unrelated data intact
        assert count("transactions", "statement_id", "s_keep") == 1
        assert count("annotations", "transaction_id", "s_keep_t") == 1

    def test_delete_statement_with_transaction_link(self, client_conn):
        # transaction_links references transactions(id) with no ON DELETE CASCADE;
        # with foreign_keys=ON, deleting the statement's transactions raises an FK
        # violation (HTTP 500) unless the links are cleared first. The link crosses
        # a statement boundary to also cover the "kept" side.
        client, conn, _ = client_conn
        self._seed_statement_with_data(conn, stmt_id="s_del")
        self._seed_statement_with_data(conn, stmt_id="s_keep")
        a, b = sorted(("s_del_t", "s_keep_t"))
        conn.execute(
            "INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES ('lnk', ?, ?, 'refund')",
            (a, b),
        )
        conn.commit()

        resp = client.delete("/api/statements/s_del")
        assert resp.status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM transaction_links WHERE id='lnk'").fetchone()[0] == 0
        # The surviving statement and its transaction are untouched.
        assert conn.execute("SELECT COUNT(*) FROM transactions WHERE id='s_keep_t'").fetchone()[0] == 1

    def test_reset_keeps_statement_and_transactions(self, client_conn):
        client, conn, _ = client_conn
        self._seed_statement_with_data(conn)
        resp = client.delete("/api/statements/s_del/data")
        assert resp.status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM statements WHERE id='s_del'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM transactions WHERE statement_id='s_del'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM annotations WHERE transaction_id='s_del_t'").fetchone()[0] == 0

    def test_delete_missing_statement_404(self, client_conn):
        client, _, _ = client_conn
        assert client.delete("/api/statements/nope").status_code == 404


class TestAnnotationJobs:
    @pytest.fixture
    def file_db_client(self, tmp_path, monkeypatch):
        """File-backed DB: the background job opens its own connection, so the
        usual shared in-memory connection can't be used here."""
        from finance_copilot.api.deps import get_db as api_get_db
        from finance_copilot.config import settings
        from finance_copilot.db import connection as dbc
        from finance_copilot.main import app

        db_path = str(tmp_path / "jobs.db")
        monkeypatch.setattr(settings, "db_path", db_path)
        conn = dbc.get_migrated_db(db_path)  # apply migrations

        def override():
            c = dbc.get_connection(db_path)
            try:
                yield c
            finally:
                c.close()

        app.dependency_overrides[api_get_db] = override
        yield TestClient(app), conn
        app.dependency_overrides.pop(api_get_db, None)
        conn.close()

    def test_job_runs_and_reports_progress(self, file_db_client):
        client, conn = file_db_client
        conn.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','test','1','2026-01')"
        )
        conn.execute(
            """INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description)
               VALUES ('t1', 's1', '2026-01-15', 199.0, 'debit', 'Netflix subscription')"""
        )
        conn.commit()

        resp = client.post("/api/annotations/auto-annotate/jobs", json={})
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # TestClient runs BackgroundTasks before returning, so the job is done
        job = client.get(f"/api/annotations/jobs/{job_id}").json()
        assert job["status"] == "completed"
        assert job["processed"] == job["total"] == 1
        assert job["result"]["rule_matched"] == 1

        ann = conn.execute("SELECT * FROM annotations WHERE transaction_id='t1'").fetchone()
        assert ann["category"] == "Entertainment"

    def test_unknown_job_404(self, file_db_client):
        client, _ = file_db_client
        assert client.get("/api/annotations/jobs/missing").status_code == 404

    def test_get_job_reaps_orphan(self, file_db_client):
        """Polling a zombie 'running' job (worker died, server up) resolves it to
        'failed' rather than spinning forever."""
        client, conn = file_db_client
        conn.execute(
            "INSERT INTO annotation_jobs (id, status, total, processed, updated_at) "
            "VALUES ('zombie','running',95,72, datetime('now','-4 days'))"
        )
        conn.commit()
        job = client.get("/api/annotations/jobs/zombie").json()
        assert job["status"] == "failed"
        assert "stale" in job["error"]

    def test_poll_does_not_block_on_live_worker_write_lock(self, file_db_client):
        """A live worker holds the write lock across a slow LLM call. Polling
        must not need that lock when nothing is stale, or the poll waits out
        busy_timeout and 500s with "database is locked" mid-run."""
        import time

        from finance_copilot.db import connection as dbc

        client, conn = file_db_client
        conn.execute(
            "INSERT INTO annotation_jobs (id, status, total, processed, updated_at) "
            "VALUES ('live','running',101,65, datetime('now'))"
        )
        conn.commit()

        from finance_copilot.config import settings

        worker = dbc.get_connection(settings.db_path)
        worker.execute("PRAGMA busy_timeout=0")
        # Uncommitted progress UPDATE, as the worker leaves it during an LLM call
        worker.execute("UPDATE annotation_jobs SET processed=66 WHERE id='live'")
        try:
            started = time.monotonic()
            resp = client.get("/api/annotations/jobs/live")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"
            assert client.get("/api/annotations/jobs?active=1").status_code == 200
            assert time.monotonic() - started < 2
        finally:
            worker.rollback()
            worker.close()

    def test_active_jobs_listing_for_reattach(self, file_db_client):
        """GET /jobs?active=1 returns the in-flight job so the UI can re-attach
        its progress card after a reload; a fresh live job is returned, a stale
        one is reaped and excluded."""
        client, conn = file_db_client
        conn.execute(
            "INSERT INTO annotation_jobs (id, status, total, processed, updated_at) "
            "VALUES ('live','running',10,3, datetime('now'))"
        )
        conn.execute(
            "INSERT INTO annotation_jobs (id, status, updated_at) "
            "VALUES ('old_done','completed', datetime('now','-1 hour'))"
        )
        conn.commit()
        active = client.get("/api/annotations/jobs?active=1").json()
        assert [j["id"] for j in active] == ["live"]

    def test_inflight_job_is_not_duplicated(self, file_db_client):
        """A second start request re-attaches to a running job instead of
        launching a duplicate, which would burn duplicate LLM calls and could
        overwrite a manual label created mid-run."""
        client, conn = file_db_client
        conn.execute(
            "INSERT INTO annotation_jobs (id, status, total, processed) VALUES ('running1','running',10,4)"
        )
        conn.commit()

        resp = client.post("/api/annotations/auto-annotate/jobs", json={})
        assert resp.json()["job_id"] == "running1"
        assert resp.json()["status"] == "running"

        count = conn.execute("SELECT COUNT(*) AS n FROM annotation_jobs").fetchone()["n"]
        assert count == 1

    def test_stale_inflight_job_is_failed_not_reattached(self, file_db_client):
        """A 'running' row whose heartbeat went quiet is an orphan (its worker
        died). Re-attaching to it would block annotation forever; a new start
        request must fail it and launch a fresh job."""
        client, conn = file_db_client
        conn.execute(
            "INSERT INTO annotation_jobs (id, status, total, processed, updated_at) "
            "VALUES ('zombie','running',95,72, datetime('now','-4 days'))"
        )
        conn.commit()

        resp = client.post("/api/annotations/auto-annotate/jobs", json={})
        assert resp.status_code == 202
        assert resp.json()["job_id"] != "zombie"

        zombie = conn.execute("SELECT status, error FROM annotation_jobs WHERE id='zombie'").fetchone()
        assert zombie["status"] == "failed"
        assert "stale" in zombie["error"]

    def test_startup_reaps_orphaned_jobs(self, file_db_client):
        """Restarting the server fails any job the previous process left
        in-flight, even a fresh one - a single-process app can't have live
        jobs at boot."""
        from finance_copilot.api.routes.annotations import reap_interrupted_jobs
        from finance_copilot.main import app

        client, conn = file_db_client
        conn.execute("INSERT INTO annotation_jobs (id, status) VALUES ('q1','queued')")
        conn.execute("INSERT INTO annotation_jobs (id, status, total, processed) VALUES ('r1','running',10,4)")
        conn.commit()

        assert reap_interrupted_jobs(conn) == 2
        statuses = {
            row["id"]: row["status"]
            for row in conn.execute("SELECT id, status FROM annotation_jobs").fetchall()
        }
        assert statuses == {"q1": "failed", "r1": "failed"}

        # And the lifespan actually invokes it: entering the client context
        # runs startup against the same file-backed DB.
        conn.execute("INSERT INTO annotation_jobs (id, status) VALUES ('q2','queued')")
        conn.commit()
        with TestClient(app):
            pass
        row = conn.execute("SELECT status, error FROM annotation_jobs WHERE id='q2'").fetchone()
        assert row["status"] == "failed"
        assert "restart" in row["error"]


class TestStatementUploadDedup:
    def _fake_parser(self):
        from finance_copilot.models.transaction import Transaction
        from finance_copilot.parsers.base import StatementParser

        class FakeParser(StatementParser):
            bank_name = "fake"
            version = "1"

            def detect(self, path, password=None):
                return True

            def parse(self, path, password=None):
                # Spans two months on purpose — period metadata must cover both
                return [
                    Transaction(txn_date=date(2026, 1, 5), amount=100.0,
                                debit_credit="debit", raw_description="TEST TXN 1"),
                    Transaction(txn_date=date(2026, 3, 2), amount=50.0,
                                debit_credit="credit", raw_description="TEST TXN 2"),
                ]

        return FakeParser()

    def test_same_file_twice_raises_duplicate(self):
        from finance_copilot.pipeline.ingest import DuplicateStatementError, ingest_pdf

        conn = _make_conn()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF fake content")
            path = tmp.name

        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=self._fake_parser()):
            stmt = ingest_pdf(path, conn=conn)
            assert stmt.file_sha256 is not None
            with pytest.raises(DuplicateStatementError):
                ingest_pdf(path, conn=conn)

        txn_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert txn_count == 2
        Path(path).unlink()
        conn.close()

    def test_period_metadata_covers_all_transactions(self):
        from finance_copilot.pipeline.ingest import ingest_pdf

        conn = _make_conn()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF period test")
            path = tmp.name

        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=self._fake_parser()):
            stmt = ingest_pdf(path, conn=conn)

        assert stmt.statement_month == "2026-01"
        row = conn.execute("SELECT period_start, period_end FROM statements WHERE id = ?", (stmt.id,)).fetchone()
        assert row["period_start"] == "2026-01-05"
        assert row["period_end"] == "2026-03-02"
        Path(path).unlink()
        conn.close()

    def test_upload_route_returns_409(self, client_conn):
        client, conn, _ = client_conn
        with patch("finance_copilot.pipeline.ingest.detect_parser", return_value=self._fake_parser()):
            files = {"file": ("stmt.pdf", b"%PDF same bytes", "application/pdf")}
            first = client.post("/api/statements/upload", files=files)
            assert first.status_code == 200
            second = client.post("/api/statements/upload", files=files)
            assert second.status_code == 409


# ---------------------------------------------------------------------------
# Apply-to-similar
# ---------------------------------------------------------------------------

def _seed_upi_txn(conn, txn_id, ann_id, name, source="llm", category="Miscellaneous", date="2026-01-10"):
    from finance_copilot.pipeline.counterparty import normalize_identity

    conn.execute(
        "INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','test','1','2026-01')"
    )
    raw_description = f"UPI/{name}/123456789012/UPI"
    # Populate counterparty_key exactly as production ingest does, so the
    # indexed same-counterparty lookup in /similar is exercised faithfully.
    conn.execute(
        """INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key)
           VALUES (?, 's1', ?, 250.0, 'debit', ?, ?)""",
        (txn_id, date, raw_description, normalize_identity(raw_description)),
    )
    ann = Annotation(id=ann_id, transaction_id=txn_id, category=category,
                     confidence=0.5, source=source)
    insert_annotation(conn, ann)
    conn.commit()


class TestApplyToSimilar:
    """Embedding service is stubbed out in tests, so candidates come from the
    same-counterparty-identity path — which must work without embeddings."""

    def test_similar_lists_same_identity_machine_rows(self, client_conn):
        client, conn, _ = client_conn
        _seed_upi_txn(conn, "t1", "a1", "SANYA PRASHANT", source="manual", category="Entertainment")
        _seed_upi_txn(conn, "t2", "a2", "SANYA PRASHANT", source="llm", category="Miscellaneous")
        _seed_upi_txn(conn, "t3", "a3", "OTHER PERSON", source="llm", category="Miscellaneous")
        resp = client.get("/api/annotations/a1/similar")
        assert resp.status_code == 200
        items = resp.json()
        assert [i["transaction_id"] for i in items] == ["t2"]
        assert items[0]["differs"] is True

    def test_similar_never_offers_human_rows(self, client_conn):
        client, conn, _ = client_conn
        _seed_upi_txn(conn, "t1", "a1", "SANYA PRASHANT", source="manual", category="Entertainment")
        _seed_upi_txn(conn, "t2", "a2", "SANYA PRASHANT", source="manual", category="Transfers")
        resp = client.get("/api/annotations/a1/similar")
        assert resp.json() == []

    def test_apply_copies_label_and_records_feedback(self, client_conn):
        client, conn, _ = client_conn
        _seed_upi_txn(conn, "t1", "a1", "SANYA PRASHANT", source="manual", category="Entertainment")
        _seed_upi_txn(conn, "t2", "a2", "SANYA PRASHANT", source="llm", category="Miscellaneous")
        resp = client.post("/api/annotations/a1/apply-to-similar", json={"transaction_ids": ["t2"]})
        assert resp.status_code == 200
        assert resp.json() == {"applied": 1, "skipped": 0}
        row = conn.execute("SELECT * FROM annotations WHERE id='a2'").fetchone()
        assert row["category"] == "Entertainment"
        assert row["source"] == "manual"
        assert row["original_source"] == "llm"
        assert row["confidence"] == 1.0
        fb = conn.execute(
            "SELECT corrected FROM feedback_stats WHERE source='llm' AND category='Miscellaneous'"
        ).fetchone()
        assert fb["corrected"] == 1

    def test_apply_skips_human_targets(self, client_conn):
        client, conn, _ = client_conn
        _seed_upi_txn(conn, "t1", "a1", "SANYA PRASHANT", source="manual", category="Entertainment")
        _seed_upi_txn(conn, "t2", "a2", "SANYA PRASHANT", source="manual", category="Transfers")
        resp = client.post("/api/annotations/a1/apply-to-similar", json={"transaction_ids": ["t2"]})
        assert resp.json() == {"applied": 0, "skipped": 1}
        row = conn.execute("SELECT category FROM annotations WHERE id='a2'").fetchone()
        assert row["category"] == "Transfers"


class TestLearnedRulesEndpoint:
    def test_lists_established_merchants_only(self, client_conn):
        client, conn, _ = client_conn
        conn.execute("INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','test','1','2026-01')")
        # SWIGGY: 3 verified Food → established. ONEOFF: 1 verified → not.
        for i in range(3):
            conn.execute("INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) VALUES (?, 's1', '2026-01-10', 100, 'debit', ?, 'SWIGGY')", (f"sw{i}", f"UPI/SWIGGY/{i}/UPI"))
            conn.execute("INSERT INTO annotations (id, transaction_id, category, confidence, source) VALUES (?, ?, 'Food & Dining', 1.0, 'manual')", (f"a{i}", f"sw{i}"))
        conn.execute("INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) VALUES ('o0', 's1', '2026-01-10', 50, 'debit', 'UPI/ONEOFF/1/UPI', 'ONEOFF')")
        conn.execute("INSERT INTO annotations (id, transaction_id, category, confidence, source) VALUES ('ao', 'o0', 'Shopping', 1.0, 'manual')")
        conn.commit()
        resp = client.get("/api/annotations/learned-rules")
        assert resp.status_code == 200
        rows = resp.json()
        assert [r["counterparty_key"] for r in rows] == ["SWIGGY"]
        assert rows[0]["support"] == 3 and rows[0]["category"] == "Food & Dining"

    def test_dismiss_removes_rule_from_listing(self, client_conn):
        client, conn, _ = client_conn
        conn.execute("INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','test','1','2026-01')")
        for i in range(3):
            conn.execute("INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) VALUES (?, 's1', '2026-01-10', 100, 'debit', ?, 'SWIGGY')", (f"sw{i}", f"UPI/SWIGGY/{i}/UPI"))
            conn.execute("INSERT INTO annotations (id, transaction_id, category, confidence, source) VALUES (?, ?, 'Food & Dining', 1.0, 'manual')", (f"a{i}", f"sw{i}"))
        conn.commit()
        assert client.get("/api/annotations/learned-rules").json()

        resp = client.delete("/api/annotations/learned-rules/SWIGGY?direction=debit")
        assert resp.status_code == 204
        assert client.get("/api/annotations/learned-rules").json() == []
        # Idempotent.
        assert client.delete("/api/annotations/learned-rules/SWIGGY?direction=debit").status_code == 204


class TestPeopleRelationship:
    def test_create_and_update_relationship(self, client_conn):
        client, conn, _ = client_conn
        resp = client.post("/api/people", json={"name": "Ananta", "upi": "ananta@oksbi", "relationship": "dad"})
        assert resp.status_code == 201
        pid = resp.json()["id"]
        assert resp.json()["relationship"] == "dad"

        upd = client.patch(f"/api/people/{pid}", json={"name": "Ananta", "upi": "ananta@oksbi", "relationship": "father"})
        assert upd.status_code == 200
        assert upd.json()["relationship"] == "father"

        # Clearing the relationship.
        cleared = client.patch(f"/api/people/{pid}", json={"name": "Ananta", "upi": "ananta@oksbi", "relationship": None})
        assert cleared.json()["relationship"] is None
