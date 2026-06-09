"""API route tests: annotation PATCH/confirm semantics, feedback recording, upload dedup."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.db.connection import init_db
from src.models.annotation import Annotation
from src.db.queries.annotations import insert_annotation


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
    from src.main import app
    from src.api.deps import get_db as api_get_db

    conn = _make_conn()
    app.dependency_overrides[api_get_db] = lambda: conn
    # Embedding service isn't available in tests — stub the best-effort embed call.
    # No context manager on TestClient: lifespan would migrate the real settings.db_path.
    with patch("src.api.routes.annotations.embed_transaction", return_value=False) as embed_mock:
        client = TestClient(app)
        yield client, conn, embed_mock
    app.dependency_overrides.pop(api_get_db, None)
    conn.close()


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
        resp = client.post("/api/annotations/a1/confirm")
        assert resp.status_code == 200
        rows = self._feedback_rows(conn)
        assert rows[("rag_direct", "Shopping")]["confirmed"] == 1

    def test_manual_source_records_no_feedback(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, source="manual")
        client.patch("/api/annotations/a1", json={"category": "Entertainment"})
        assert self._feedback_rows(conn) == {}


class TestConfirmFlow:
    def test_confirm_sets_confidence_and_provenance(self, client_conn):
        client, conn, _ = client_conn
        _seed_annotated_txn(conn, source="llm", confidence=0.6)
        resp = client.post("/api/annotations/a1/confirm")
        body = resp.json()
        assert body["confidence"] == 1.0
        assert body["source"] == "manual"
        assert body["original_source"] == "llm"


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
        page1 = client.get("/api/transactions?limit=2").json()
        assert [r["id"] for r in page1] == ["t0", "t1"]
        page2 = client.get(f"/api/transactions?limit=2&after={page1[-1]['id']}").json()
        assert [r["id"] for r in page2] == ["t2", "t3"]
        page3 = client.get(f"/api/transactions?limit=2&after={page2[-1]['id']}").json()
        assert [r["id"] for r in page3] == ["t4"]

    def test_unknown_cursor_ignored(self, client_conn):
        client, conn, _ = client_conn
        self._seed_many(conn, 2)
        rows = client.get("/api/transactions?after=missing&limit=10").json()
        assert len(rows) == 2


class TestStatementUploadDedup:
    def _fake_parser(self):
        from src.models.transaction import Transaction

        class FakeParser:
            bank_name = "fake"
            version = "1"

            def detect(self, path, password=None):
                return True

            def parse(self, path, password=None):
                return [Transaction(
                    txn_date=date(2026, 1, 5), amount=100.0,
                    debit_credit="debit", raw_description="TEST TXN",
                )]

        return FakeParser()

    def test_same_file_twice_raises_duplicate(self):
        from src.pipeline.ingest import DuplicateStatementError, ingest_pdf

        conn = _make_conn()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF fake content")
            path = tmp.name

        with patch("src.pipeline.ingest.detect_parser", return_value=self._fake_parser()):
            stmt = ingest_pdf(path, conn=conn)
            assert stmt.file_sha256 is not None
            with pytest.raises(DuplicateStatementError):
                ingest_pdf(path, conn=conn)

        txn_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert txn_count == 1
        Path(path).unlink()
        conn.close()

    def test_upload_route_returns_409(self, client_conn):
        client, conn, _ = client_conn
        with patch("src.pipeline.ingest.detect_parser", return_value=self._fake_parser()):
            files = {"file": ("stmt.pdf", b"%PDF same bytes", "application/pdf")}
            first = client.post("/api/statements/upload", files=files)
            assert first.status_code == 200
            second = client.post("/api/statements/upload", files=files)
            assert second.status_code == 409
