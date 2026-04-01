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

    def test_source_is_rule(self):
        txn = {"id": "t7", "raw_description": "Uber ride payment", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.source == "rule"

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

        # RAG embedding unavailable → falls through to plain LLM
        with patch("src.pipeline.annotate.get_embedding_single", side_effect=Exception("unavailable")), \
             patch("src.pipeline.annotate.annotate_transaction_llm", return_value=llm_result):
            result = auto_annotate(self.conn)

        assert result.llm_annotated == 1
        assert result.rule_matched == 0
        ann = get_annotation_by_transaction(self.conn, "t3")
        assert ann is not None
        assert ann["category"] == "Shopping"

    def test_llm_failure_counted(self):
        from src.pipeline.annotate import auto_annotate

        _insert_txn(self.conn, "t4", "completely unknown merchant 99999")

        with patch("src.pipeline.annotate.get_embedding_single", side_effect=Exception("unavailable")), \
             patch("src.pipeline.annotate.annotate_transaction_llm", return_value=None):
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

        with patch("src.pipeline.annotate.get_embedding_single", side_effect=Exception("unavailable")), \
             patch("src.pipeline.annotate.annotate_transaction_llm", return_value=llm_result):
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
        for key in ("total_processed", "rule_matched", "rag_direct_annotated",
                    "rag_prompted_annotated", "llm_annotated",
                    "llm_failed", "low_confidence", "already_annotated"):
            assert key in data, f"missing key: {key}"

    def test_rule_match_reflected_in_response(self):
        _insert_txn(self.conn, "api_t1", "Netflix subscription")

        response = self.client.post("/annotations/auto-annotate", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["rule_matched"] == 1
        assert data["total_processed"] == 1


# ---------------------------------------------------------------------------
# Embed text builder tests
# ---------------------------------------------------------------------------

class TestBuildEmbedText:
    def test_basic(self):
        from src.pipeline.embed import build_embed_text
        txn = {"debit_credit": "debit", "amount": 500.0,
               "raw_description": "SWIGGY ORDER", "upi_meta": None}
        result = build_embed_text(txn)
        assert result == "debit 500.0 SWIGGY ORDER"

    def test_with_upi_note(self):
        from src.pipeline.embed import build_embed_text
        txn = {"debit_credit": "debit", "amount": 500.0,
               "raw_description": "UPI TRANSFER",
               "upi_meta": json.dumps({"note": "food order"})}
        result = build_embed_text(txn)
        assert "food order" in result
        assert "500.0" in result

    def test_missing_upi_note_key(self):
        from src.pipeline.embed import build_embed_text
        txn = {"debit_credit": "credit", "amount": 10000.0,
               "raw_description": "SALARY CREDIT",
               "upi_meta": json.dumps({"note": None})}
        result = build_embed_text(txn)
        assert result == "credit 10000.0 SALARY CREDIT"


# ---------------------------------------------------------------------------
# Few-shot prompt builder tests
# ---------------------------------------------------------------------------

class TestFewShotPrompt:
    def test_prompt_includes_examples(self):
        from src.pipeline.llm import _build_fewshot_user_prompt
        txn = {"id": "t1", "raw_description": "food delivery", "upi_meta": None,
               "amount": 500, "debit_credit": "debit", "txn_date": "2026-01-15"}
        examples = [{
            "raw_description": "Swiggy order", "upi_note": "",
            "amount": 450, "debit_credit": "debit",
            "category": "Food & Dining", "subcategory": "Food Delivery",
            "merchant": "Swiggy",
        }]
        result = _build_fewshot_user_prompt(txn, ["Food & Dining"], examples)
        assert "Example 1:" in result
        assert "Swiggy" in result
        assert "Now classify this transaction:" in result
        assert "food delivery" in result

    def test_prompt_shows_category_subcategory(self):
        from src.pipeline.llm import _build_fewshot_user_prompt
        txn = {"id": "t1", "raw_description": "some txn", "upi_meta": None,
               "amount": 100, "debit_credit": "debit", "txn_date": "2026-01-15"}
        examples = [{
            "raw_description": "IRCTC booking", "upi_note": "",
            "amount": 800, "debit_credit": "debit",
            "category": "Travel", "subcategory": "Train", "merchant": "IRCTC",
        }]
        result = _build_fewshot_user_prompt(txn, ["Travel"], examples)
        assert "Travel > Train" in result

    def test_empty_examples_still_renders_transaction(self):
        from src.pipeline.llm import _build_fewshot_user_prompt
        txn = {"id": "t1", "raw_description": "mystery", "upi_meta": None,
               "amount": 100, "debit_credit": "debit", "txn_date": "2026-01-15"}
        result = _build_fewshot_user_prompt(txn, ["Uncategorized"], [])
        assert "mystery" in result


# ---------------------------------------------------------------------------
# RAG pipeline integration tests
# ---------------------------------------------------------------------------

class TestRAGPipeline:
    def setup_method(self):
        self.conn = _make_conn()
        _insert_statement(self.conn)

    def teardown_method(self):
        self.conn.close()

    def _insert_donor(self, txn_id: str, description: str, category: str,
                      subcategory: str | None = None, merchant: str | None = None) -> None:
        """Insert a transaction + annotation to act as a RAG donor."""
        from src.db.queries.annotations import insert_annotation
        from src.models.annotation import Annotation
        _insert_txn(self.conn, txn_id, description)
        insert_annotation(self.conn, Annotation(
            transaction_id=txn_id,
            category=category,
            subcategory=subcategory,
            merchant=merchant,
            confidence=0.95,
            source="rule",
        ))
        self.conn.commit()

    def test_rag_direct_copies_annotation(self):
        """When cosine similarity >= rag_direct_threshold, annotation is copied directly."""
        from src.pipeline.annotate import auto_annotate

        self._insert_donor("donor_1", "Swiggy food order", "Food & Dining", "Food Delivery", "Swiggy")
        _insert_txn(self.conn, "target_1", "unknown food delivery app")

        mock_vec = [0.1] * 768
        # distance=0.05 → similarity=0.95 (above 0.92 threshold)
        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=[
                 {"transaction_id": "donor_1", "distance": 0.05},
             ]):
            result = auto_annotate(self.conn, transaction_ids=["target_1"])

        assert result.rag_direct_annotated == 1
        assert result.rag_prompted_annotated == 0
        ann = get_annotation_by_transaction(self.conn, "target_1")
        assert ann is not None
        assert ann["source"] == "rag_direct"
        assert ann["category"] == "Food & Dining"
        assert ann["confidence"] == pytest.approx(0.95)

    def test_rag_prompted_uses_llm_with_examples(self):
        """When similarity is below direct threshold, LLM is called with few-shot examples."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse

        self._insert_donor("donor_2", "Some shop transaction", "Shopping")
        _insert_txn(self.conn, "target_2", "ambiguous merchant name")

        mock_vec = [0.1] * 768
        llm_result = AnnotationResponse(
            category="Shopping", subcategory="General Retail",
            merchant=None, tags=["shopping"], confidence=0.75,
        )
        # distance=0.15 → similarity=0.85 (below 0.92 threshold)
        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=[
                 {"transaction_id": "donor_2", "distance": 0.15},
             ]), \
             patch("src.pipeline.annotate.annotate_transaction_llm_with_examples", return_value=llm_result):
            result = auto_annotate(self.conn, transaction_ids=["target_2"])

        assert result.rag_prompted_annotated == 1
        assert result.rag_direct_annotated == 0
        ann = get_annotation_by_transaction(self.conn, "target_2")
        assert ann is not None
        assert ann["source"] == "rag_prompted"

    def test_embedding_unavailable_falls_through_to_plain_llm(self):
        """When embedding service is down, pipeline falls through to plain LLM."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse

        _insert_txn(self.conn, "target_3", "mystery transaction xyz")
        llm_result = AnnotationResponse(category="Uncategorized", confidence=0.5)

        with patch("src.pipeline.annotate.get_embedding_single", side_effect=Exception("connection refused")), \
             patch("src.pipeline.annotate.annotate_transaction_llm", return_value=llm_result):
            result = auto_annotate(self.conn, transaction_ids=["target_3"])

        assert result.llm_annotated == 1
        assert result.rag_direct_annotated == 0
        assert result.rag_prompted_annotated == 0

    def test_no_similar_results_falls_through_to_plain_llm(self):
        """When vec_items is empty, RAG finds nothing and plain LLM handles it."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse

        _insert_txn(self.conn, "target_4", "new unique merchant")
        mock_vec = [0.1] * 768
        llm_result = AnnotationResponse(category="Shopping", confidence=0.7)

        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=[]), \
             patch("src.pipeline.annotate.annotate_transaction_llm", return_value=llm_result):
            result = auto_annotate(self.conn, transaction_ids=["target_4"])

        assert result.llm_annotated == 1

    def test_rule_source_label_is_rule(self):
        """Rules now produce source='rule' instead of 'model'."""
        from src.pipeline.annotate import auto_annotate

        _insert_txn(self.conn, "target_5", "Swiggy food order")
        result = auto_annotate(self.conn, transaction_ids=["target_5"])

        ann = get_annotation_by_transaction(self.conn, "target_5")
        assert ann["source"] == "rule"
        assert result.rule_matched == 1


# ---------------------------------------------------------------------------
# Disambiguation rule tests
# ---------------------------------------------------------------------------

class TestDisambiguationRules:
    def test_uber_eats_maps_to_food(self):
        txn = {"id": "t1", "raw_description": "UPI/UBEREATS/PAYMENT", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Food & Dining"
        assert result.subcategory == "Food Delivery"
        assert result.merchant == "Uber Eats"

    def test_uber_food_in_note_maps_to_food(self):
        txn = {"id": "t2", "raw_description": "UPI TRANSFER",
               "upi_meta": json.dumps({"note": "uber food order"})}
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Food & Dining"

    def test_uber_ride_stays_transport(self):
        txn = {"id": "t3", "raw_description": "Uber ride payment", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Transport"
        assert result.merchant == "Uber"

    def test_amazon_prime_video_maps_to_entertainment(self):
        txn = {"id": "t4", "raw_description": "AMZN prime video subscription", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Entertainment"
        assert result.merchant == "Amazon Prime"

    def test_amazon_prime_membership_maps_to_entertainment(self):
        txn = {"id": "t5", "raw_description": "amazon prime membership", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Entertainment"

    def test_amazon_shopping_stays_shopping(self):
        txn = {"id": "t6", "raw_description": "AMAZON PAYMENTS INDIA shopping order", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Shopping"

    def test_aws_maps_to_financial(self):
        txn = {"id": "t7", "raw_description": "AMZN AWS services payment", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Financial"
        assert result.merchant == "AWS"

    def test_disambiguation_confidence_is_095(self):
        txn = {"id": "t8", "raw_description": "Uber eats delivery", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.confidence == 0.95


# ---------------------------------------------------------------------------
# Agreement and margin factor unit tests
# ---------------------------------------------------------------------------

class TestAgreementFactor:
    def test_all_agree_returns_one(self):
        from src.pipeline.annotate import _compute_agreement_factor
        matches = [
            {"category": "Shopping"},
            {"category": "Shopping"},
            {"category": "Shopping"},
        ]
        assert _compute_agreement_factor(matches, "Shopping") == 1.0

    def test_empty_matches_returns_one(self):
        from src.pipeline.annotate import _compute_agreement_factor
        assert _compute_agreement_factor([], "Shopping") == 1.0

    def test_three_of_five_agree(self):
        from src.pipeline.annotate import _compute_agreement_factor
        matches = [
            {"category": "Shopping"},
            {"category": "Shopping"},
            {"category": "Shopping"},
            {"category": "Entertainment"},
            {"category": "Entertainment"},
        ]
        factor = _compute_agreement_factor(matches, "Shopping")
        # majority_fraction = 3/5 = 0.6, exponent = 0.3
        expected = 0.6 ** 0.3
        assert abs(factor - expected) < 1e-6

    def test_four_of_five_agree_gentle_penalty(self):
        from src.pipeline.annotate import _compute_agreement_factor
        matches = [{"category": "Shopping"}] * 4 + [{"category": "Transport"}]
        factor = _compute_agreement_factor(matches, "Shopping")
        expected = 0.8 ** 0.3
        assert abs(factor - expected) < 1e-6
        assert factor > 0.9  # gentle penalty

    def test_one_of_five_agree_strong_penalty(self):
        from src.pipeline.annotate import _compute_agreement_factor
        matches = [{"category": "Shopping"}] + [{"category": "Transport"}] * 4
        factor = _compute_agreement_factor(matches, "Shopping")
        expected = 0.2 ** 0.3
        assert abs(factor - expected) < 1e-6
        assert factor < 0.7  # significant penalty


class TestMarginFactor:
    def test_no_different_category_returns_one(self):
        from src.pipeline.annotate import _compute_margin_factor
        assert _compute_margin_factor(0.05, None) == 1.0

    def test_large_margin_returns_one(self):
        from src.pipeline.annotate import _compute_margin_factor
        assert _compute_margin_factor(0.05, 0.15) == 1.0  # margin=0.10 >= 0.08

    def test_zero_margin_returns_085(self):
        from src.pipeline.annotate import _compute_margin_factor
        factor = _compute_margin_factor(0.05, 0.05)  # margin=0
        assert abs(factor - 0.85) < 1e-6

    def test_half_margin_interpolates(self):
        from src.pipeline.annotate import _compute_margin_factor
        # margin = 0.04, rag_margin_safe = 0.08 → midpoint → 0.85 + 0.075 = 0.925
        factor = _compute_margin_factor(0.05, 0.09)  # margin=0.04
        assert abs(factor - 0.925) < 1e-6

    def test_exact_safe_margin_returns_one(self):
        from src.pipeline.annotate import _compute_margin_factor
        factor = _compute_margin_factor(0.05, 0.13)  # margin=0.08 exactly
        assert abs(factor - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Novelty gate tests
# ---------------------------------------------------------------------------

class TestNoveltyGate:
    def setup_method(self):
        self.conn = _make_conn()
        _insert_statement(self.conn)

    def teardown_method(self):
        self.conn.close()

    def test_high_distance_falls_through_to_plain_llm(self):
        """When best similarity < rag_similarity_floor, skip RAG and use plain LLM."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse

        _insert_txn(self.conn, "target_novel", "completely novel merchant xyz")
        llm_result = AnnotationResponse(category="Uncategorized", confidence=0.5)
        mock_vec = [0.1] * 768
        # distance=0.40 → similarity=0.60, below floor of 0.65
        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=[
                 {"transaction_id": "some_donor", "distance": 0.40},
             ]), \
             patch("src.pipeline.annotate.annotate_transaction_llm", return_value=llm_result):
            result = auto_annotate(self.conn, transaction_ids=["target_novel"])

        assert result.llm_annotated == 1
        assert result.rag_direct_annotated == 0
        assert result.rag_prompted_annotated == 0

    def test_above_floor_proceeds_to_rag(self):
        """When best similarity >= rag_similarity_floor, RAG proceeds normally."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse
        from src.db.queries.annotations import insert_annotation
        from src.models.annotation import Annotation

        # Insert a real donor so _build_examples_from_similar returns examples
        _insert_txn(self.conn, "donor_known", "known merchant description")
        insert_annotation(self.conn, Annotation(
            transaction_id="donor_known", category="Shopping", confidence=0.95, source="rule",
        ))
        self.conn.commit()

        _insert_txn(self.conn, "target_known", "somewhat known merchant")
        llm_result = AnnotationResponse(category="Shopping", confidence=0.75)
        mock_vec = [0.1] * 768
        # distance=0.30 → similarity=0.70, above floor of 0.65
        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=[
                 {"transaction_id": "donor_known", "distance": 0.30},
             ]), \
             patch("src.pipeline.annotate.annotate_transaction_llm_with_examples", return_value=llm_result):
            result = auto_annotate(self.conn, transaction_ids=["target_known"])

        # RAG proceeds (rag_prompted path used since similarity < rag_direct_threshold)
        assert result.rag_prompted_annotated == 1
        assert result.llm_annotated == 0


# ---------------------------------------------------------------------------
# LLM dampening tests
# ---------------------------------------------------------------------------

class TestLLMDampening:
    def setup_method(self):
        self.conn = _make_conn()
        _insert_statement(self.conn)

    def teardown_method(self):
        self.conn.close()

    def test_plain_llm_confidence_dampened(self):
        """Plain LLM confidence is multiplied by llm_confidence_dampen (0.85)."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse
        from src.db.queries.annotations import get_annotation_by_transaction

        _insert_txn(self.conn, "t_llm", "mystery vendor")
        llm_result = AnnotationResponse(category="Shopping", confidence=0.9)

        with patch("src.pipeline.annotate.get_embedding_single", side_effect=Exception("unavailable")), \
             patch("src.pipeline.annotate.annotate_transaction_llm", return_value=llm_result):
            auto_annotate(self.conn, transaction_ids=["t_llm"])

        ann = get_annotation_by_transaction(self.conn, "t_llm")
        assert ann is not None
        assert abs(ann["confidence"] - round(0.9 * 0.85, 4)) < 1e-4

    def test_rag_prompted_confidence_dampened(self):
        """RAG prompted confidence is multiplied by llm_confidence_dampen_rag (0.92)."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse
        from src.db.queries.annotations import get_annotation_by_transaction, insert_annotation
        from src.models.annotation import Annotation

        # Insert a real donor so _build_examples_from_similar returns examples
        _insert_txn(self.conn, "donor_rag", "known shopping vendor")
        insert_annotation(self.conn, Annotation(
            transaction_id="donor_rag", category="Shopping", confidence=0.95, source="rule",
        ))
        self.conn.commit()

        _insert_txn(self.conn, "t_rag", "ambiguous vendor")
        llm_result = AnnotationResponse(category="Shopping", confidence=0.9)
        mock_vec = [0.1] * 768

        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=[
                 {"transaction_id": "donor_rag", "distance": 0.15},  # below rag_direct_threshold
             ]), \
             patch("src.pipeline.annotate.annotate_transaction_llm_with_examples", return_value=llm_result):
            auto_annotate(self.conn, transaction_ids=["t_rag"])

        ann = get_annotation_by_transaction(self.conn, "t_rag")
        assert ann is not None
        assert abs(ann["confidence"] - round(0.9 * 0.92, 4)) < 1e-4


# ---------------------------------------------------------------------------
# RAG direct confidence with agreement + margin factors
# ---------------------------------------------------------------------------

class TestRAGDirectAmbiguity:
    def setup_method(self):
        self.conn = _make_conn()
        _insert_statement(self.conn)

    def teardown_method(self):
        self.conn.close()

    def _insert_donor(self, txn_id, description, category, source="rule"):
        from src.db.queries.annotations import insert_annotation
        from src.models.annotation import Annotation
        _insert_txn(self.conn, txn_id, description)
        insert_annotation(self.conn, Annotation(
            transaction_id=txn_id, category=category, confidence=0.95, source=source,
        ))
        self.conn.commit()

    def test_rag_direct_discounted_when_category_disagreement(self):
        """When top-K matches disagree on category, rag_direct confidence is penalized."""
        from src.pipeline.annotate import auto_annotate
        from src.db.queries.annotations import get_annotation_by_transaction

        self._insert_donor("donor_shop_1", "online shopping", "Shopping")
        self._insert_donor("donor_shop_2", "buy stuff", "Shopping")
        self._insert_donor("donor_shop_3", "retail purchase", "Shopping")
        self._insert_donor("donor_ent_1", "streaming service", "Entertainment")
        self._insert_donor("donor_ent_2", "video subscription", "Entertainment")

        _insert_txn(self.conn, "target_ambig", "ambiguous vendor payment")
        mock_vec = [0.1] * 768
        # distance=0.06 → cosine_similarity=0.94 (above rag_direct_threshold=0.92)
        similar = [
            {"transaction_id": "donor_shop_1", "distance": 0.06},
            {"transaction_id": "donor_shop_2", "distance": 0.07},
            {"transaction_id": "donor_shop_3", "distance": 0.08},
            {"transaction_id": "donor_ent_1", "distance": 0.08},
            {"transaction_id": "donor_ent_2", "distance": 0.09},
        ]
        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=similar):
            auto_annotate(self.conn, transaction_ids=["target_ambig"])

        ann = get_annotation_by_transaction(self.conn, "target_ambig")
        assert ann is not None
        # 3/5 agreement → factor ≈ 0.858, plus margin factor
        # Confidence must be below raw cosine_similarity (0.94)
        assert ann["confidence"] < 0.94

    def test_rag_direct_unpenalized_when_all_agree(self):
        """When all top-K matches agree on category, confidence equals cosine similarity."""
        from src.pipeline.annotate import auto_annotate
        from src.db.queries.annotations import get_annotation_by_transaction

        for i in range(3):
            self._insert_donor(f"donor_a{i}", f"food delivery {i}", "Food & Dining")

        _insert_txn(self.conn, "target_clear", "food order")
        mock_vec = [0.1] * 768
        similar = [
            {"transaction_id": "donor_a0", "distance": 0.05},
            {"transaction_id": "donor_a1", "distance": 0.06},
            {"transaction_id": "donor_a2", "distance": 0.07},
        ]
        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=similar):
            auto_annotate(self.conn, transaction_ids=["target_clear"])

        ann = get_annotation_by_transaction(self.conn, "target_clear")
        assert ann is not None
        # All agree, no different category → both factors = 1.0
        assert abs(ann["confidence"] - round(0.95, 4)) < 1e-4


# ---------------------------------------------------------------------------
# System prompt calibration test
# ---------------------------------------------------------------------------

class TestSystemPromptCalibration:
    def test_system_prompt_contains_calibration_guidance(self):
        from src.pipeline.llm import _SYSTEM_PROMPT
        assert "0.95" in _SYSTEM_PROMPT
        assert "0.85" in _SYSTEM_PROMPT
        assert "0.70" in _SYSTEM_PROMPT
        assert "conservative" in _SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Embeddings API endpoint tests
# ---------------------------------------------------------------------------

class TestEmbeddingsEndpoint:
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

    def test_generate_returns_counts(self):
        with patch("src.api.routes.embeddings.embed_annotated_transactions",
                   return_value={"embedded": 3, "skipped": 1}):
            response = self.client.post("/embeddings/generate", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["embedded"] == 3
        assert data["skipped"] == 1

    def test_generate_with_statement_id(self):
        with patch("src.api.routes.embeddings.embed_annotated_transactions",
                   return_value={"embedded": 5, "skipped": 0}) as mock_embed:
            response = self.client.post("/embeddings/generate", json={"statement_id": "stmt_01"})
        assert response.status_code == 200
        mock_embed.assert_called_once()
        call_kwargs = mock_embed.call_args
        assert call_kwargs[0][1] == "stmt_01" or call_kwargs[1].get("statement_id") == "stmt_01"

    def test_stats_endpoint_shape(self):
        response = self.client.get("/embeddings/stats/stmt_01")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "embedded" in data
        assert "annotated" in data

    def test_stats_returns_zero_counts_for_empty_statement(self):
        response = self.client.get("/embeddings/stats/stmt_01")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["embedded"] == 0
        assert data["annotated"] == 0
