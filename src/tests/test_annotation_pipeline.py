"""Tests for embedding + retrieval + annotation pipeline integration."""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.db.connection import init_db
from src.db.queries.annotations import get_annotation_by_transaction
from src.models.annotation import AnnotationCreate
from src.pipeline.rules import apply_rules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    """In-memory SQLite with schema applied."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def _insert_statement(conn: sqlite3.Connection, stmt_id: str = "stmt_01") -> str:
    conn.execute(
        "INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES (?, ?, ?, ?)",
        (stmt_id, "test", "1", "2026-01"),
    )
    conn.commit()
    return stmt_id


def _insert_txn(
    conn: sqlite3.Connection,
    txn_id: str,
    description: str,
    upi_note: str = "",
    stmt_id: str = "stmt_01",
    amount: float = 100.0,
    debit_credit: str = "debit",
) -> dict:
    upi_meta = json.dumps({"note": upi_note}) if upi_note else None
    conn.execute(
        """INSERT INTO transactions
           (id, statement_id, txn_date, amount, debit_credit, raw_description, upi_meta)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (txn_id, stmt_id, "2026-01-15", amount, debit_credit, description, upi_meta),
    )
    conn.commit()
    return {"id": txn_id, "raw_description": description, "upi_meta": upi_meta,
            "amount": amount, "debit_credit": debit_credit, "txn_date": "2026-01-15"}


# ---------------------------------------------------------------------------
# Rule engine tests
# ---------------------------------------------------------------------------

class TestRuleEngine:
    def test_swiggy_matches(self):
        txn = {"id": "t1", "raw_description": "UPI/Swiggy/payment", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Food & Dining"
        assert result.subcategory == "Food Delivery"
        assert result.merchant == "Swiggy"

    def test_amazon_matches(self):
        txn = {"id": "t2", "raw_description": "AMAZON PAYMENTS INDIA", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Shopping"

    def test_unknown_returns_none(self):
        txn = {"id": "t3", "raw_description": "Some random description XYZ", "upi_meta": None}
        result = apply_rules(txn)
        assert result is None

    def test_case_insensitive(self):
        txn = {"id": "t4", "raw_description": "ZOMATO ORDER PAYMENT", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.merchant == "Zomato"

    def test_upi_note_matching(self):
        txn = {
            "id": "t5",
            "raw_description": "UPI transfer",
            "upi_meta": json.dumps({"note": "swiggy food order"}),
        }
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Food & Dining"

    def test_confidence_is_095(self):
        txn = {"id": "t6", "raw_description": "Netflix subscription", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.confidence == 0.95

    def test_source_is_model(self):
        txn = {"id": "t7", "raw_description": "Uber ride payment", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.source == "model"

    def test_irctc_maps_to_travel(self):
        txn = {"id": "t8", "raw_description": "IRCTC ticket booking", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Travel"
        assert result.subcategory == "Train"

    def test_salary_matches_raw_description(self):
        txn = {"id": "t9", "raw_description": "SAL CREDIT ACME CORP", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Income"

    def test_tags_populated(self):
        txn = {"id": "t10", "raw_description": "Netflix subscription", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert "ott" in result.tags


# ---------------------------------------------------------------------------
# LLM client tests (mocked httpx)
# ---------------------------------------------------------------------------

class TestLLMClient:
    def _valid_response_payload(self) -> dict:
        content = json.dumps({
            "category": "Food & Dining",
            "subcategory": "Restaurants",
            "merchant": "Local Eatery",
            "tags": ["food"],
            "confidence": 0.8,
        })
        return {"message": {"content": content}}

    def test_valid_response_parsed(self):
        from src.pipeline.llm import annotate_transaction_llm, AnnotationResponse

        txn = {"id": "t1", "raw_description": "Some restaurant", "upi_meta": None,
               "amount": 500, "debit_credit": "debit", "txn_date": "2026-01-15"}
        categories = ["Food & Dining > Restaurants"]

        mock_resp = MagicMock()
        mock_resp.json.return_value = self._valid_response_payload()
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_resp):
            result = annotate_transaction_llm(txn, categories)

        assert result is not None
        assert isinstance(result, AnnotationResponse)
        assert result.category == "Food & Dining"
        assert result.confidence == 0.8

    def test_invalid_json_returns_none_after_retries(self):
        from src.pipeline.llm import annotate_transaction_llm

        txn = {"id": "t1", "raw_description": "mystery", "upi_meta": None,
               "amount": 100, "debit_credit": "debit", "txn_date": "2026-01-15"}

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"message": {"content": "not json at all !!"}}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_resp), patch("time.sleep"):
            result = annotate_transaction_llm(txn, ["Food & Dining"], max_retries=2)

        assert result is None

    def test_http_error_returns_none(self):
        import httpx as _httpx
        from src.pipeline.llm import annotate_transaction_llm

        txn = {"id": "t1", "raw_description": "error case", "upi_meta": None,
               "amount": 100, "debit_credit": "debit", "txn_date": "2026-01-15"}

        with patch("httpx.post", side_effect=_httpx.HTTPError("connection refused")), \
             patch("time.sleep"):
            result = annotate_transaction_llm(txn, ["Food & Dining"], max_retries=1)

        assert result is None

    def test_timeout_returns_none(self):
        import httpx as _httpx
        from src.pipeline.llm import annotate_transaction_llm

        txn = {"id": "t1", "raw_description": "slow response", "upi_meta": None,
               "amount": 100, "debit_credit": "debit", "txn_date": "2026-01-15"}

        with patch("httpx.post", side_effect=_httpx.TimeoutException("timeout")), \
             patch("time.sleep"):
            result = annotate_transaction_llm(txn, ["Food & Dining"], max_retries=0)

        assert result is None


# ---------------------------------------------------------------------------
# Pipeline integration tests (in-memory SQLite, mocked LLM)
# ---------------------------------------------------------------------------

class TestAutoAnnotatePipeline:
    def setup_method(self):
        self.conn = _make_conn()
        _insert_statement(self.conn)

    def teardown_method(self):
        self.conn.close()

    def test_rule_only_path(self):
        from src.pipeline.annotate import auto_annotate

        _insert_txn(self.conn, "t1", "Swiggy food order")
        result = auto_annotate(self.conn)

        assert result.rule_matched == 1
        assert result.llm_annotated == 0
        assert result.total_processed == 1
        assert result.already_annotated == 0

    def test_annotations_persisted(self):
        from src.pipeline.annotate import auto_annotate

        _insert_txn(self.conn, "t2", "Netflix subscription")
        auto_annotate(self.conn)

        ann = get_annotation_by_transaction(self.conn, "t2")
        assert ann is not None
        assert ann["category"] == "Entertainment"

    def test_llm_fallback_path(self):
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse

        _insert_txn(self.conn, "t3", "unknown vendor xyz 12345")

        llm_result = AnnotationResponse(
            category="Shopping",
            subcategory="General Retail",
            merchant="Unknown Vendor",
            tags=["shopping"],
            confidence=0.7,
        )

        with patch("src.pipeline.annotate.annotate_transaction_llm", return_value=llm_result):
            result = auto_annotate(self.conn)

        assert result.llm_annotated == 1
        assert result.rule_matched == 0
        ann = get_annotation_by_transaction(self.conn, "t3")
        assert ann is not None
        assert ann["category"] == "Shopping"

    def test_llm_failure_counted(self):
        from src.pipeline.annotate import auto_annotate

        _insert_txn(self.conn, "t4", "completely unknown merchant 99999")

        with patch("src.pipeline.annotate.annotate_transaction_llm", return_value=None):
            result = auto_annotate(self.conn)

        assert result.llm_failed == 1
        assert result.llm_annotated == 0
        assert get_annotation_by_transaction(self.conn, "t4") is None

    def test_skip_already_annotated(self):
        from src.pipeline.annotate import auto_annotate
        from src.models.annotation import Annotation
        from src.db.queries.annotations import insert_annotation

        _insert_txn(self.conn, "t5", "Amazon shopping")
        existing = Annotation(
            transaction_id="t5",
            category="Shopping",
            subcategory="Online Shopping",
            confidence=1.0,
            source="manual",
        )
        insert_annotation(self.conn, existing)
        self.conn.commit()

        result = auto_annotate(self.conn)
        assert result.already_annotated == 1
        assert result.total_processed == 0

    def test_low_confidence_counted(self):
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse

        _insert_txn(self.conn, "t6", "mystery transaction")

        llm_result = AnnotationResponse(
            category="Uncategorized",
            confidence=0.3,  # below default threshold of 0.85
        )

        with patch("src.pipeline.annotate.annotate_transaction_llm", return_value=llm_result):
            result = auto_annotate(self.conn)

        assert result.low_confidence == 1

    def test_filter_by_statement_id(self):
        from src.pipeline.annotate import auto_annotate

        _insert_statement(self.conn, "stmt_02")
        _insert_txn(self.conn, "t7", "Zomato order", stmt_id="stmt_01")
        _insert_txn(self.conn, "t8", "Uber ride", stmt_id="stmt_02")

        result = auto_annotate(self.conn, statement_id="stmt_01")
        assert result.total_processed == 1

    def test_filter_by_transaction_ids(self):
        from src.pipeline.annotate import auto_annotate

        _insert_txn(self.conn, "t9", "Swiggy food")
        _insert_txn(self.conn, "t10", "Zomato order")

        result = auto_annotate(self.conn, transaction_ids=["t9"])
        assert result.total_processed == 1
        assert result.rule_matched == 1


# ---------------------------------------------------------------------------
# API endpoint test
# ---------------------------------------------------------------------------

class TestAutoAnnotateEndpoint:
    def setup_method(self):
        from src.main import app
        from src.api.deps import get_db as api_get_db

        self.conn = _make_conn()
        _insert_statement(self.conn)

        app.dependency_overrides[api_get_db] = lambda: self.conn
        self.client = TestClient(app)

    def teardown_method(self):
        from src.main import app
        from src.api.deps import get_db as api_get_db

        app.dependency_overrides.pop(api_get_db, None)
        self.conn.close()

    def test_returns_auto_annotate_result_shape(self):
        response = self.client.post("/annotations/auto-annotate", json={})
        assert response.status_code == 200
        data = response.json()
        for key in ("total_processed", "rule_matched", "llm_annotated",
                    "llm_failed", "low_confidence", "already_annotated"):
            assert key in data, f"missing key: {key}"

    def test_rule_match_reflected_in_response(self):
        _insert_txn(self.conn, "api_t1", "Netflix subscription")

        response = self.client.post("/annotations/auto-annotate", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["rule_matched"] == 1
        assert data["total_processed"] == 1
