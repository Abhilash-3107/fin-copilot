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
    from src.pipeline.counterparty import normalize_identity
    upi_meta = json.dumps({"note": upi_note}) if upi_note else None
    conn.execute(
        """INSERT INTO transactions
           (id, statement_id, txn_date, amount, debit_credit, raw_description, upi_meta, counterparty_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (txn_id, stmt_id, "2026-01-15", amount, debit_credit, description, upi_meta, normalize_identity(description)),
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
# Rule false-positive corpus: substrings that used to fire word-boundary-less
# ---------------------------------------------------------------------------

class TestRuleFalsePositives:
    @pytest.mark.parametrize("description", [
        "INSURANCE PREMIUM COLLECTION AXA",   # 'premium' must not match 'emi'
        "ACCOUNT INFO UPDATE CHARGE",         # 'info' must not match 'nfo'
        "GRANOLA BARS SUPERSTORE",            # 'granola' must not match 'ola'
        "COCA COLA BEVERAGES",                # 'cola' must not match 'ola'
        "NAVI TECHNOLOGIES PAYMENT",          # 'navi' must not match 'vi'
        "COMMITMENT FEE CHARGED",             # 'commitment' must not match 'mmt'
        "REPUBLIC DAY OFFER CASHBACK",        # 'republic' must not match 'lic'
        "PINOLA RESTAURANT BILL",             # 'pinola' must not match 'ola'
    ])
    def test_no_substring_false_positive(self, description):
        txn = {"id": "fp", "raw_description": description, "upi_meta": None}
        result = apply_rules(txn)
        wrong = {"Loan EMI", "Mutual Fund SIP", "Cab & Auto", "Mobile Recharge"}
        assert result is None or result.subcategory not in wrong, (
            f"{description!r} falsely matched {result.category}/{result.subcategory}"
        )

    @pytest.mark.parametrize("description,expected_subcategory", [
        ("HOME LOAN EMI DEBIT", "Loan EMI"),
        ("MUTUAL FUND NFO SUBSCRIPTION", "Mutual Fund SIP"),
        ("OLA RIDE 1234", "Cab & Auto"),
        ("OLACABS BANGALORE", "Cab & Auto"),
        ("VI RECHARGE 299", "Mobile Recharge"),
        ("LIC PREMIUM PAYMENT", "Insurance Premium"),
        ("MMT*FLIGHT BOOKING", None),  # MakeMyTrip rule has no subcategory
    ])
    def test_word_boundary_still_matches(self, description, expected_subcategory):
        txn = {"id": "tp", "raw_description": description, "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None, f"{description!r} should match a rule"
        assert result.subcategory == expected_subcategory


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
        response = self.client.post("/api/annotations/auto-annotate", json={})
        assert response.status_code == 200
        data = response.json()
        for key in ("total_processed", "rule_matched", "rag_direct_annotated",
                    "rag_prompted_annotated", "llm_annotated",
                    "llm_failed", "low_confidence", "already_annotated"):
            assert key in data, f"missing key: {key}"

    def test_rule_match_reflected_in_response(self):
        _insert_txn(self.conn, "api_t1", "Netflix subscription")

        response = self.client.post("/api/annotations/auto-annotate", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["rule_matched"] == 1
        assert data["total_processed"] == 1


# ---------------------------------------------------------------------------
# Embed text builder tests
# ---------------------------------------------------------------------------

class TestBuildEmbedText:
    def test_basic(self):
        # Amount is deliberately excluded — it's per-transaction noise that
        # dilutes the merchant signal the retriever depends on.
        from src.pipeline.embed import build_embed_text
        txn = {"debit_credit": "debit", "amount": 500.0,
               "raw_description": "SWIGGY ORDER", "upi_meta": None}
        result = build_embed_text(txn)
        # Lowercased: nomic-embed-text mangles ALL-CAPS bank text.
        assert result == "debit swiggy order"

    def test_with_upi_note(self):
        from src.pipeline.embed import build_embed_text
        txn = {"debit_credit": "debit", "amount": 500.0,
               "raw_description": "UPI TRANSFER",
               "upi_meta": json.dumps({"note": "food order"})}
        result = build_embed_text(txn)
        assert "food order" in result
        assert "500.0" not in result  # amount no longer embedded

    def test_missing_upi_note_key(self):
        from src.pipeline.embed import build_embed_text
        txn = {"debit_credit": "credit", "amount": 10000.0,
               "raw_description": "SALARY CREDIT",
               "upi_meta": json.dumps({"note": None})}
        result = build_embed_text(txn)
        assert result == "credit salary credit"

    def test_strips_upi_reference(self):
        # The rotating numeric ref must be dropped so two visits to the same
        # merchant embed identically.
        from src.pipeline.embed import build_embed_text
        a = {"debit_credit": "debit", "amount": 228.0,
             "raw_description": "UPI/OBEROIFC tucksh/121013717523/UPI", "upi_meta": None}
        b = {"debit_credit": "debit", "amount": 805.0,
             "raw_description": "UPI/OBEROIFC tucksh/120580534937/UPI", "upi_meta": None}
        assert build_embed_text(a) == build_embed_text(b) == "debit upi/oberoifc tucksh"

    def test_keeps_meaningful_upi_note(self):
        from src.pipeline.embed import build_embed_text
        txn = {"debit_credit": "debit", "amount": 300.0,
               "raw_description": "UPI/ANANTA KUMAR BO/121066925569/movie tickets",
               "upi_meta": None}
        assert build_embed_text(txn) == "debit upi/ananta kumar bo/movie tickets"

    def test_non_upi_description_untouched(self):
        from src.pipeline.embed import build_embed_text
        txn = {"debit_credit": "debit", "amount": 100.0,
               "raw_description": "PCD/1280/ETERNAL LIMITED/GURGAON180226/16:21",
               "upi_meta": None}
        assert build_embed_text(txn) == "debit pcd/1280/eternal limited/gurgaon180226/16:21"


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

    def test_prompt_includes_majority_hint_and_guardrail(self):
        from src.pipeline.llm import _build_fewshot_user_prompt
        txn = {"id": "t1", "raw_description": "UPI/UNKNOWN NAME/123/UPI", "upi_meta": None,
               "amount": 100, "debit_credit": "debit", "txn_date": "2026-01-15"}
        examples = [{
            "raw_description": "UPI/DRIVER/1/UPI", "upi_note": "",
            "amount": 100, "debit_credit": "debit",
            "category": "Transport", "subcategory": "Cab & Auto", "merchant": None,
        }]
        result = _build_fewshot_user_prompt(
            txn, ["Transport"], examples, majority_category="Transport", majority_count=4
        )
        # Agreement hint surfaces the count and category explicitly
        assert "4 of the examples" in result
        assert "Transport" in result
        # Guardrail discourages off-example categories
        assert "Prefer a category that appears among the examples" in result

    def test_prompt_omits_hint_when_no_majority(self):
        from src.pipeline.llm import _build_fewshot_user_prompt
        txn = {"id": "t1", "raw_description": "some txn", "upi_meta": None,
               "amount": 100, "debit_credit": "debit", "txn_date": "2026-01-15"}
        examples = [{
            "raw_description": "x", "upi_note": "", "amount": 1, "debit_credit": "debit",
            "category": "Shopping", "subcategory": None, "merchant": None,
        }]
        result = _build_fewshot_user_prompt(txn, ["Shopping"], examples)
        # No "N of the examples" hint line, but the guardrail still appears
        assert "of the examples above were categorized" not in result
        assert "Prefer a category that appears among the examples" in result


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
# rag_prompted off-example backstop + majority hint
# ---------------------------------------------------------------------------

class TestRagPromptedOffExample:
    """The LLM should not get an auto-accepted pass for a category that none of
    the retrieved examples used (the 'invents Peer Transfer' failure)."""

    def setup_method(self):
        self.conn = _make_conn()
        _insert_statement(self.conn)

    def teardown_method(self):
        self.conn.close()

    def _insert_donor(self, txn_id, description, category, subcategory=None, source="manual"):
        from src.db.queries.annotations import insert_annotation
        from src.models.annotation import Annotation
        _insert_txn(self.conn, txn_id, description)
        insert_annotation(self.conn, Annotation(
            transaction_id=txn_id, category=category, subcategory=subcategory,
            confidence=0.95, source=source,
        ))
        self.conn.commit()

    def test_majority_category_helper(self):
        from src.pipeline.annotate import _majority_category
        assert _majority_category([]) == (None, 0)
        assert _majority_category(["Transport", "Transport", "Food & Dining"]) == ("Transport", 2)

    def test_off_example_category_confidence_capped(self):
        """LLM returns 'Transfers' but no example was Transfers → confidence capped
        below threshold so it lands in the review queue rather than auto-accepted."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse
        from src.config import settings

        # All donors are Transport — none are Transfers.
        for i in range(4):
            self._insert_donor(f"donor_t{i}", f"UPI/DRIVER {i}/1/UPI", "Transport", "Cab & Auto")
        _insert_txn(self.conn, "target_off", "UPI/UNKNOWN NAME/9/UPI")

        # LLM ignores the examples and picks Transfers (the prior-driven failure).
        llm_result = AnnotationResponse(category="Transfers", subcategory="Peer Transfer", confidence=0.9)
        mock_vec = [0.1] * 768
        similar = [{"transaction_id": f"donor_t{i}", "distance": 0.2} for i in range(4)]

        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=similar), \
             patch("src.pipeline.annotate.annotate_transaction_llm_with_examples", return_value=llm_result):
            result = auto_annotate(self.conn, transaction_ids=["target_off"])

        ann = get_annotation_by_transaction(self.conn, "target_off")
        assert ann is not None
        assert ann["source"] == "rag_prompted"
        assert ann["category"] == "Transfers"  # not overridden — just distrusted
        assert ann["confidence"] <= settings.rag_offexample_confidence_cap
        assert ann["confidence"] < settings.confidence_threshold
        assert result.low_confidence == 1

    def test_in_example_category_not_capped(self):
        """When the LLM picks a category present in the examples, normal dampening applies."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse
        from src.config import settings

        for i in range(4):
            self._insert_donor(f"donor_in{i}", f"UPI/DRIVER {i}/1/UPI", "Transport", "Cab & Auto")
        _insert_txn(self.conn, "target_in", "UPI/SOME NAME/9/UPI")

        llm_result = AnnotationResponse(category="Transport", subcategory="Cab & Auto", confidence=0.9)
        mock_vec = [0.1] * 768
        similar = [{"transaction_id": f"donor_in{i}", "distance": 0.2} for i in range(4)]

        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=similar), \
             patch("src.pipeline.annotate.annotate_transaction_llm_with_examples", return_value=llm_result):
            auto_annotate(self.conn, transaction_ids=["target_in"])

        ann = get_annotation_by_transaction(self.conn, "target_in")
        assert ann is not None
        assert ann["category"] == "Transport"
        # 0.9 * rag dampen (0.92) = 0.828, well above the off-example cap of 0.5
        assert ann["confidence"] > settings.rag_offexample_confidence_cap

    def test_split_trusted_neighbors_deferred_to_review(self):
        """When trusted donors are split with no clear winner, confidence is capped
        below threshold so the txn routes to review (selective classification).

        Mirrors the real ANSHU YADAV case: a small UPI to an unknown name whose
        amount-neighbors are part Transport (cab), part Transfers (family)."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse
        from src.config import settings

        # 2 trusted Transport + 2 trusted Transfers → no clear winner (50/50).
        self._insert_donor("d_cab1", "UPI/DRIVER A/1/UPI", "Transport", "Cab & Auto")
        self._insert_donor("d_cab2", "UPI/DRIVER B/2/UPI", "Transport", "Cab & Auto")
        self._insert_donor("d_fam1", "UPI/SIBLING/3/UPI", "Transfers", "Family")
        self._insert_donor("d_fam2", "UPI/COUSIN/4/UPI", "Transfers", "Family")
        _insert_txn(self.conn, "target_split", "UPI/UNKNOWN PERSON/9/UPI")

        # LLM picks Transfers (in examples, so off-example doesn't fire) AND is
        # itself unsure (raw conf below the defer ceiling) → genuinely undecidable.
        llm_result = AnnotationResponse(category="Transfers", subcategory="Family", confidence=0.7)
        mock_vec = [0.1] * 768
        similar = [
            {"transaction_id": "d_fam1", "distance": 0.20},
            {"transaction_id": "d_cab1", "distance": 0.21},
            {"transaction_id": "d_fam2", "distance": 0.22},
            {"transaction_id": "d_cab2", "distance": 0.23},
        ]
        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=similar), \
             patch("src.pipeline.annotate.annotate_transaction_llm_with_examples", return_value=llm_result):
            result = auto_annotate(self.conn, transaction_ids=["target_split"])

        ann = get_annotation_by_transaction(self.conn, "target_split")
        assert ann is not None
        assert ann["confidence"] <= settings.rag_defer_confidence_cap
        assert ann["confidence"] < settings.confidence_threshold
        assert result.low_confidence == 1

    def test_clear_trusted_majority_not_deferred(self):
        """A clear trusted majority (>= consensus floor) is not deferred."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse
        from src.config import settings

        # 4 trusted Transport vs 0 others → unanimous, well above the floor.
        for i in range(4):
            self._insert_donor(f"d_clear{i}", f"UPI/DRIVER {i}/1/UPI", "Transport", "Cab & Auto")
        _insert_txn(self.conn, "target_clear2", "UPI/NAME/9/UPI")

        llm_result = AnnotationResponse(category="Transport", subcategory="Cab & Auto", confidence=0.7)
        mock_vec = [0.1] * 768
        similar = [{"transaction_id": f"d_clear{i}", "distance": 0.20 + i * 0.01} for i in range(4)]
        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=similar), \
             patch("src.pipeline.annotate.annotate_transaction_llm_with_examples", return_value=llm_result):
            auto_annotate(self.conn, transaction_ids=["target_clear2"])

        ann = get_annotation_by_transaction(self.conn, "target_clear2")
        assert ann is not None
        # Clear consensus (share=1.0 >= floor) → not deferred even though LLM is unsure.
        assert ann["confidence"] > settings.rag_defer_confidence_cap

    def test_confident_llm_not_deferred_despite_split_neighbors(self):
        """A confident, merchant-grounded LLM answer is NOT deferred even when the
        amount-driven neighbor vote is split (the Zomato/Miya Kebabs case)."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse
        from src.config import settings

        # Split trusted donors: 2 Food, 2 Transport.
        self._insert_donor("ds_f1", "UPI/REST A/1/UPI", "Food & Dining", "Restaurants")
        self._insert_donor("ds_f2", "UPI/REST B/2/UPI", "Food & Dining", "Restaurants")
        self._insert_donor("ds_t1", "UPI/DRIVER A/3/UPI", "Transport", "Cab & Auto")
        self._insert_donor("ds_t2", "UPI/DRIVER B/4/UPI", "Transport", "Cab & Auto")
        _insert_txn(self.conn, "target_confident", "UPI/Zomato8759/9/UPI")

        # LLM recognizes the merchant and is confident → must not be deferred.
        llm_result = AnnotationResponse(category="Food & Dining", subcategory="Food Delivery", confidence=0.95)
        mock_vec = [0.1] * 768
        similar = [
            {"transaction_id": "ds_f1", "distance": 0.20},
            {"transaction_id": "ds_t1", "distance": 0.21},
            {"transaction_id": "ds_f2", "distance": 0.22},
            {"transaction_id": "ds_t2", "distance": 0.23},
        ]
        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=similar), \
             patch("src.pipeline.annotate.annotate_transaction_llm_with_examples", return_value=llm_result):
            auto_annotate(self.conn, transaction_ids=["target_confident"])

        ann = get_annotation_by_transaction(self.conn, "target_confident")
        assert ann is not None
        assert ann["category"] == "Food & Dining"
        assert ann["confidence"] > settings.rag_defer_confidence_cap  # not deferred

    def test_majority_hint_passed_to_llm(self):
        """The example-category majority is forwarded to the LLM call."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse

        for i in range(3):
            self._insert_donor(f"donor_m{i}", f"UPI/DRIVER {i}/1/UPI", "Transport", "Cab & Auto")
        self._insert_donor("donor_other", "UPI/SHOP/1/UPI", "Shopping")
        _insert_txn(self.conn, "target_m", "UPI/NAME/9/UPI")

        llm_result = AnnotationResponse(category="Transport", confidence=0.8)
        mock_vec = [0.1] * 768
        similar = [{"transaction_id": f"donor_m{i}", "distance": 0.2} for i in range(3)]
        similar.append({"transaction_id": "donor_other", "distance": 0.25})

        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=similar), \
             patch("src.pipeline.annotate.annotate_transaction_llm_with_examples", return_value=llm_result) as llm_mock:
            auto_annotate(self.conn, transaction_ids=["target_m"])

        # majority_category="Transport", majority_count=3 passed positionally
        args, kwargs = llm_mock.call_args
        assert "Transport" in args or kwargs.get("majority_category") == "Transport"


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

    def test_aws_maps_to_finances(self):
        txn = {"id": "t7", "raw_description": "AMZN AWS services payment", "upi_meta": None}
        result = apply_rules(txn)
        assert result is not None
        assert result.category == "Finances"
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
# Donor-pool dedup + source-weighted voting
# ---------------------------------------------------------------------------

class TestDonorVoting:
    def test_donor_weight_trusted_vs_machine(self):
        from src.pipeline.annotate import _donor_weight
        from src.config import settings
        assert _donor_weight("manual") == 1.0
        assert _donor_weight("rule") == 1.0
        assert _donor_weight("imported") == 1.0
        assert _donor_weight("llm") == settings.rag_machine_donor_weight
        assert _donor_weight("rag_prompted") == settings.rag_machine_donor_weight
        assert _donor_weight(None) == settings.rag_machine_donor_weight

    def test_counterparty_key_prefers_vpa(self):
        from src.pipeline.annotate import _counterparty_key
        match = {
            "transaction_id": "x", "distance": 0.1,
            "annotation": {"merchant": "District Dining"},
            "upi_meta": json.dumps({"vpa": "dist@hdfc"}),
            "raw_description": "UPI/DISTRICT DINING/1/UPI",
        }
        assert _counterparty_key(match) == "vpa:dist@hdfc"

    def test_counterparty_key_falls_back_to_merchant(self):
        from src.pipeline.annotate import _counterparty_key
        match = {
            "transaction_id": "x", "distance": 0.1,
            "annotation": {"merchant": "District Dining"},
            "upi_meta": None, "raw_description": "UPI/DD/1/UPI",
        }
        assert _counterparty_key(match) == "merchant:district dining"

    def test_dedup_collapses_recurring_merchant(self):
        """3 instances of the same merchant collapse to one (nearest) vote."""
        from src.pipeline.annotate import _dedup_donors
        matches = [
            {"transaction_id": "a", "distance": 0.30, "category": "Food & Dining",
             "source": "rag_prompted", "annotation": {"merchant": "District Dining"},
             "upi_meta": None, "raw_description": "x"},
            {"transaction_id": "b", "distance": 0.20, "category": "Food & Dining",
             "source": "rag_prompted", "annotation": {"merchant": "District Dining"},
             "upi_meta": None, "raw_description": "x"},
            {"transaction_id": "c", "distance": 0.25, "category": "Food & Dining",
             "source": "rag_prompted", "annotation": {"merchant": "District Dining"},
             "upi_meta": None, "raw_description": "x"},
            {"transaction_id": "d", "distance": 0.22, "category": "Transport",
             "source": "manual", "annotation": {"merchant": "Some Driver"},
             "upi_meta": None, "raw_description": "y"},
        ]
        deduped = _dedup_donors(matches)
        assert len(deduped) == 2  # one District Dining + one driver
        dd = [m for m in deduped if m["annotation"]["merchant"] == "District Dining"][0]
        assert dd["distance"] == 0.20  # kept the nearest instance

    def test_weighted_vote_human_beats_repeated_machine(self):
        """One human Transport label outweighs three machine Food labels of the
        same merchant after dedup + source weighting (the RAMESH case)."""
        from src.pipeline.annotate import _dedup_donors, _weighted_trusted_vote
        matches = [
            {"transaction_id": "f1", "distance": 0.20, "category": "Food & Dining",
             "source": "rag_prompted", "annotation": {"merchant": "District Dining"},
             "upi_meta": None, "raw_description": "x"},
            {"transaction_id": "f2", "distance": 0.25, "category": "Food & Dining",
             "source": "rag_prompted", "annotation": {"merchant": "District Dining"},
             "upi_meta": None, "raw_description": "x"},
            {"transaction_id": "f3", "distance": 0.30, "category": "Food & Dining",
             "source": "rag_prompted", "annotation": {"merchant": "District Dining"},
             "upi_meta": None, "raw_description": "x"},
            {"transaction_id": "t1", "distance": 0.22, "category": "Transport",
             "source": "manual", "annotation": {"merchant": "Driver"},
             "upi_meta": None, "raw_description": "y"},
        ]
        deduped = _dedup_donors(matches)
        winner, share, trusted = _weighted_trusted_vote(deduped)
        # After dedup: Food (machine, 0.25) vs Transport (manual, 1.0) → Transport wins.
        assert winner == "Transport"
        assert share > 0.5
        assert trusted == 1.0

    def test_weighted_vote_split_has_low_share(self):
        from src.pipeline.annotate import _weighted_trusted_vote
        matches = [
            {"category": "Transport", "source": "manual", "distance": 0.2,
             "annotation": {}, "upi_meta": None},
            {"category": "Transfers", "source": "manual", "distance": 0.2,
             "annotation": {}, "upi_meta": None},
        ]
        winner, share, trusted = _weighted_trusted_vote(matches)
        assert share == 0.5  # tied → below the consensus floor
        assert trusted == 2.0

    def test_weighted_vote_empty(self):
        from src.pipeline.annotate import _weighted_trusted_vote
        assert _weighted_trusted_vote([]) == (None, 0.0, 0.0)


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
# UPI metadata extraction + known-person matching
# ---------------------------------------------------------------------------

class TestUpiParsing:
    NOISE = ["UPI", "NA", "NO REMARKS", "-"]

    def _parse(self, raw):
        from src.parsers.upi import parse_upi_description
        result = parse_upi_description(raw, self.NOISE)
        return json.loads(result) if result else None

    def test_vpa_ref_note_extracted(self):
        meta = self._parse("UPI/merchant@okaxis/118030236405/food order")
        assert meta == {"vpa": "merchant@okaxis", "ref": "118030236405", "note": "food order"}

    def test_ref_as_last_segment_is_not_a_note(self):
        meta = self._parse("UPI/Agoda Company P/118030236405")
        assert meta["ref"] == "118030236405"
        assert meta["note"] is None

    def test_noise_note_is_none(self):
        meta = self._parse("UPI/someone@ybl/12345678/NA")
        assert meta["vpa"] == "someone@ybl"
        assert meta["note"] is None

    def test_non_upi_returns_none(self):
        assert self._parse("NEFT TRANSFER FROM ACME") is None

    def test_no_vpa_or_ref(self):
        meta = self._parse("UPI/SOME SHOP/groceries")
        assert meta == {"vpa": None, "ref": None, "note": "groceries"}


class TestKnownPersonMatching:
    def _people(self):
        return [("Rahul", "rahul@okaxis")]

    def test_exact_vpa_match(self):
        from src.pipeline.annotate import _match_known_person
        txn = {"id": "t1", "raw_description": "UPI/rahul@okaxis/118030236405/dinner",
               "upi_meta": json.dumps({"vpa": "rahul@okaxis", "ref": "118030236405", "note": "dinner"})}
        result = _match_known_person(txn, self._people())
        assert result is not None
        assert result.merchant == "Rahul"
        assert result.subcategory == "Peer Transfer"

    def test_different_vpa_does_not_substring_match(self):
        """'rahul@okaxis' must not match 'notrahul@okaxis' when a VPA is present."""
        from src.pipeline.annotate import _match_known_person
        txn = {"id": "t2", "raw_description": "UPI/notrahul@okaxis/12345678/x",
               "upi_meta": json.dumps({"vpa": "notrahul@okaxis", "ref": "12345678", "note": "x"})}
        assert _match_known_person(txn, self._people()) is None

    def test_legacy_rows_fall_back_to_substring(self):
        from src.pipeline.annotate import _match_known_person
        txn = {"id": "t3", "raw_description": "IMPS rahul@okaxis transfer", "upi_meta": None}
        result = _match_known_person(txn, self._people())
        assert result is not None
        assert result.merchant == "Rahul"

    def test_family_relationship_maps_to_family_subcategory(self):
        from src.pipeline.annotate import _match_known_person
        txn = {"id": "t4", "raw_description": "UPI/ananta@oksbi/1/rent",
               "upi_meta": json.dumps({"vpa": "ananta@oksbi", "ref": "1", "note": "rent"})}
        result = _match_known_person(txn, [("Ananta", "ananta@oksbi", "dad")])
        assert result is not None
        assert result.category == "Transfers"
        assert result.subcategory == "Family"
        assert "family" in result.tags

    def test_non_family_relationship_stays_peer_transfer(self):
        from src.pipeline.annotate import _match_known_person
        txn = {"id": "t5", "raw_description": "UPI/rahul@okaxis/1/x",
               "upi_meta": json.dumps({"vpa": "rahul@okaxis", "ref": "1", "note": "x"})}
        result = _match_known_person(txn, [("Rahul", "rahul@okaxis", "friend")])
        assert result is not None
        assert result.subcategory == "Peer Transfer"


# ---------------------------------------------------------------------------
# Stage-4 description dedup cache
# ---------------------------------------------------------------------------

class TestLLMDescriptionDedup:
    def test_identical_descriptions_call_llm_once(self):
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse

        conn = _make_conn()
        _insert_statement(conn)
        for i in range(3):
            _insert_txn(conn, f"dup{i}", "NACH RECURRING GYM FEE", amount=999.0)

        llm_result = AnnotationResponse(category="Personal Care", confidence=0.8)
        with patch("src.pipeline.annotate.get_embedding_single", side_effect=Exception("down")), \
             patch("src.pipeline.annotate.annotate_transaction_llm", return_value=llm_result) as llm_mock:
            result = auto_annotate(conn)

        assert llm_mock.call_count == 1
        assert result.llm_annotated == 3
        for i in range(3):
            assert get_annotation_by_transaction(conn, f"dup{i}")["category"] == "Personal Care"
        conn.close()

    def test_progress_callback_reports_each_transaction(self):
        from src.pipeline.annotate import auto_annotate

        conn = _make_conn()
        _insert_statement(conn)
        _insert_txn(conn, "p1", "Netflix subscription")
        _insert_txn(conn, "p2", "Swiggy order")

        calls = []
        auto_annotate(conn, progress_cb=lambda done, total: calls.append((done, total)))
        assert calls == [(1, 2), (2, 2)]
        conn.close()


# ---------------------------------------------------------------------------
# Category validation tests (enum schema + server-side normalization)
# ---------------------------------------------------------------------------

CATEGORY_LIST = [
    "Food & Dining", "Food & Dining > Restaurants", "Food & Dining > Groceries",
    "Shopping", "Shopping > Online Shopping",
    "Transport", "Transfers", "Entertainment", "Uncategorized",
]


class TestCategoryValidation:
    def test_top_level_categories_collapse(self):
        from src.pipeline.llm import top_level_categories
        tops = top_level_categories(CATEGORY_LIST)
        assert tops == ["Food & Dining", "Shopping", "Transport", "Transfers",
                        "Entertainment", "Uncategorized"]

    def test_schema_constrains_category_enum(self):
        from src.pipeline.llm import _response_schema
        schema = _response_schema(CATEGORY_LIST)
        assert schema["properties"]["category"]["enum"] == [
            "Food & Dining", "Shopping", "Transport", "Transfers",
            "Entertainment", "Uncategorized",
        ]

    def test_empty_category_list_leaves_schema_unconstrained(self):
        from src.pipeline.llm import _response_schema
        schema = _response_schema([])
        assert "enum" not in schema["properties"]["category"]

    def test_exact_category_passes_through(self):
        from src.pipeline.annotate import _normalize_category
        assert _normalize_category("Shopping", CATEGORY_LIST) == "Shopping"

    def test_case_mismatch_normalized(self):
        from src.pipeline.annotate import _normalize_category
        assert _normalize_category("shopping", CATEGORY_LIST) == "Shopping"

    def test_partial_name_normalized(self):
        from src.pipeline.annotate import _normalize_category
        assert _normalize_category("Food", CATEGORY_LIST) == "Food & Dining"

    def test_close_misspelling_normalized(self):
        from src.pipeline.annotate import _normalize_category
        assert _normalize_category("Entertainmnet", CATEGORY_LIST) == "Entertainment"

    def test_hallucinated_category_falls_back(self):
        from src.pipeline.annotate import _normalize_category
        assert _normalize_category("Subscriptions", CATEGORY_LIST) == "Uncategorized"

    def test_hallucinated_category_persisted_as_uncategorized(self):
        """End-to-end: LLM returns a made-up category, stored row is valid."""
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse

        conn = _make_conn()
        _insert_statement(conn)
        _insert_txn(conn, "t_hallu", "weird merchant qqq")
        llm_result = AnnotationResponse(category="Crypto Stuff", confidence=0.9)

        with patch("src.pipeline.annotate.get_embedding_single", side_effect=Exception("down")), \
             patch("src.pipeline.annotate.annotate_transaction_llm", return_value=llm_result):
            auto_annotate(conn)

        ann = get_annotation_by_transaction(conn, "t_hallu")
        assert ann["category"] == "Uncategorized"
        conn.close()


# ---------------------------------------------------------------------------
# Bayesian calibration math
# ---------------------------------------------------------------------------

class TestCalibration:
    def setup_method(self):
        self.conn = _make_conn()

    def teardown_method(self):
        self.conn.close()

    def _record(self, n, feedback_type, source="llm", category="Shopping"):
        from src.db.queries.feedback_stats import record_feedback
        for _ in range(n):
            record_feedback(self.conn, source, category, feedback_type)
        self.conn.commit()

    def test_no_feedback_returns_static_base(self):
        from src.pipeline.calibration import get_calibrated_dampening
        from src.config import settings
        assert get_calibrated_dampening(self.conn, "llm", "Shopping") == pytest.approx(settings.llm_confidence_dampen)
        assert get_calibrated_dampening(self.conn, "rag_prompted", "Shopping") == pytest.approx(settings.llm_confidence_dampen_rag)

    @pytest.mark.parametrize("source", ["rule", "rag_direct", "manual"])
    def test_undampened_sources_return_one(self, source):
        from src.pipeline.calibration import get_calibrated_dampening
        assert get_calibrated_dampening(self.conn, source, "Shopping") == 1.0

    def test_confirmations_raise_dampening(self):
        from src.pipeline.calibration import get_calibrated_dampening
        self._record(5, "confirmed")
        # prior: alpha=0.85*5=4.25, beta=0.75; +5 confirmed → 9.25/10
        assert get_calibrated_dampening(self.conn, "llm", "Shopping") == pytest.approx(0.925)

    def test_corrections_lower_dampening(self):
        from src.pipeline.calibration import get_calibrated_dampening
        self._record(5, "corrected")
        # alpha=4.25, beta=0.75+5 → 4.25/10
        assert get_calibrated_dampening(self.conn, "llm", "Shopping") == pytest.approx(0.425)

    def test_refinements_count_half(self):
        from src.pipeline.calibration import get_calibrated_dampening
        self._record(2, "refined")
        # alpha=4.25+1, beta=0.75 → 5.25/6
        assert get_calibrated_dampening(self.conn, "llm", "Shopping") == pytest.approx(5.25 / 6.0)

    def test_feedback_is_scoped_per_source_and_category(self):
        from src.pipeline.calibration import get_calibrated_dampening
        from src.config import settings
        self._record(5, "corrected", source="llm", category="Shopping")
        # other category and other source unaffected
        assert get_calibrated_dampening(self.conn, "llm", "Travel") == pytest.approx(settings.llm_confidence_dampen)
        assert get_calibrated_dampening(self.conn, "rag_prompted", "Shopping") == pytest.approx(settings.llm_confidence_dampen_rag)

    def test_single_event_does_not_swing_score(self):
        from src.pipeline.calibration import get_calibrated_dampening
        from src.config import settings
        self._record(1, "corrected")
        value = get_calibrated_dampening(self.conn, "llm", "Shopping")
        assert abs(value - settings.llm_confidence_dampen) < 0.15


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
            response = self.client.post("/api/embeddings/generate", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["embedded"] == 3
        assert data["skipped"] == 1

    def test_generate_with_statement_id(self):
        with patch("src.api.routes.embeddings.embed_annotated_transactions",
                   return_value={"embedded": 5, "skipped": 0}) as mock_embed:
            response = self.client.post("/api/embeddings/generate", json={"statement_id": "stmt_01"})
        assert response.status_code == 200
        mock_embed.assert_called_once()
        call_kwargs = mock_embed.call_args
        assert call_kwargs[0][1] == "stmt_01" or call_kwargs[1].get("statement_id") == "stmt_01"

    def test_stats_endpoint_shape(self):
        response = self.client.get("/api/embeddings/stats/stmt_01")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "embedded" in data
        assert "annotated" in data

    def test_stats_returns_zero_counts_for_empty_statement(self):
        response = self.client.get("/api/embeddings/stats/stmt_01")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["embedded"] == 0
        assert data["annotated"] == 0


# ---------------------------------------------------------------------------
# Dev-mode reasoning trace
# ---------------------------------------------------------------------------

class TestReasoningTrace:
    """The pipeline captures a per-annotation reasoning trace into the reasoning
    column, but only when dev mode is on (the runtime app_settings value)."""

    def setup_method(self):
        self.conn = _make_conn()
        _insert_statement(self.conn)

    def teardown_method(self):
        self.conn.close()

    def _enable_dev_mode(self):
        from src.db.queries.app_settings import set_dev_mode
        set_dev_mode(self.conn, True)
        self.conn.commit()

    def _stored_reasoning(self, txn_id: str):
        ann = get_annotation_by_transaction(self.conn, txn_id)
        assert ann is not None
        return ann.get("reasoning"), ann

    def test_no_trace_when_dev_mode_off(self):
        """With dev_mode off (default), the reasoning column stays NULL."""
        from src.pipeline.annotate import auto_annotate

        _insert_txn(self.conn, "t_off", "Swiggy food order")
        auto_annotate(self.conn, transaction_ids=["t_off"])

        reasoning, _ = self._stored_reasoning("t_off")
        assert reasoning is None

    def test_rule_trace_captured(self):
        from src.pipeline.annotate import auto_annotate

        self._enable_dev_mode()
        _insert_txn(self.conn, "t_rule", "Netflix subscription")
        auto_annotate(self.conn, transaction_ids=["t_rule"])

        reasoning, ann = self._stored_reasoning("t_rule")
        assert reasoning is not None
        trace = json.loads(reasoning)
        assert trace["stage"] == "rule"
        assert trace["matched_rule"]  # merchant or category
        assert trace["final_confidence"] == pytest.approx(ann["confidence"])

    def test_rag_prompted_trace_has_neighbours_and_llm_reasoning(self):
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse
        from src.db.queries.annotations import insert_annotation
        from src.models.annotation import Annotation

        self._enable_dev_mode()
        _insert_txn(self.conn, "donor_tr", "known shopping vendor")
        insert_annotation(self.conn, Annotation(
            transaction_id="donor_tr", category="Shopping", confidence=0.95, source="rule",
        ))
        self.conn.commit()
        _insert_txn(self.conn, "t_rag", "ambiguous vendor")

        llm_result = AnnotationResponse(
            category="Shopping", confidence=0.8, reasoning="Looks like a retail purchase.",
        )
        mock_vec = [0.1] * 768
        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=[
                 {"transaction_id": "donor_tr", "distance": 0.15},
             ]), \
             patch("src.pipeline.annotate.annotate_transaction_llm_with_examples", return_value=llm_result):
            auto_annotate(self.conn, transaction_ids=["t_rag"])

        reasoning, ann = self._stored_reasoning("t_rag")
        assert reasoning is not None
        trace = json.loads(reasoning)
        assert trace["stage"] == "rag_prompted"
        assert len(trace["neighbours"]) >= 1
        assert trace["neighbours"][0]["category"] == "Shopping"
        assert trace["neighbours"][0]["similarity"] == pytest.approx(0.85)
        assert trace["llm_reasoning"] == "Looks like a retail purchase."
        assert trace["raw_confidence"] == pytest.approx(0.8)
        assert trace["final_confidence"] == pytest.approx(ann["confidence"])

    def test_plain_llm_trace_has_dampening(self):
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse

        self._enable_dev_mode()
        _insert_txn(self.conn, "t_plain", "mystery vendor xyz")
        llm_result = AnnotationResponse(
            category="Shopping", confidence=0.9, reasoning="No close history; best guess.",
        )
        with patch("src.pipeline.annotate.get_embedding_single", side_effect=Exception("down")), \
             patch("src.pipeline.annotate.annotate_transaction_llm", return_value=llm_result):
            auto_annotate(self.conn, transaction_ids=["t_plain"])

        reasoning, ann = self._stored_reasoning("t_plain")
        trace = json.loads(reasoning)
        assert trace["stage"] == "llm"
        assert trace["raw_confidence"] == pytest.approx(0.9)
        assert trace["dampening_factor"] is not None
        assert trace["llm_reasoning"] == "No close history; best guess."
        assert trace["final_confidence"] == pytest.approx(ann["confidence"])

    def test_review_queue_endpoint_exposes_trace_in_dev_mode(self):
        """The review-queue endpoint parses the stored JSON only when dev_mode is on."""
        from src.main import app
        from src.api.deps import get_db as api_get_db
        from src.db.queries.app_settings import set_dev_mode
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse

        self._enable_dev_mode()
        _insert_txn(self.conn, "t_q", "mystery vendor for queue")
        llm_result = AnnotationResponse(category="Uncategorized", confidence=0.3,
                                        reasoning="Weak signal.")
        with patch("src.pipeline.annotate.get_embedding_single", side_effect=Exception("down")), \
             patch("src.pipeline.annotate.annotate_transaction_llm", return_value=llm_result):
            auto_annotate(self.conn, transaction_ids=["t_q"])

        app.dependency_overrides[api_get_db] = lambda: self.conn
        try:
            client = TestClient(app)
            resp = client.get("/api/annotations/review-queue")
            assert resp.status_code == 200
            item = next(i for i in resp.json() if i["transaction_id"] == "t_q")
            assert item["reasoning"] is not None
            assert item["reasoning"]["stage"] == "llm"

            # Dev mode off → no reasoning field leaks out.
            set_dev_mode(self.conn, False)
            self.conn.commit()
            resp = client.get("/api/annotations/review-queue")
            item = next(i for i in resp.json() if i["transaction_id"] == "t_q")
            assert "reasoning" not in item
        finally:
            app.dependency_overrides.pop(api_get_db, None)


class TestConfigEndpoint:
    def setup_method(self):
        from src.main import app
        from src.api.deps import get_db as api_get_db
        self.conn = _make_conn()
        app.dependency_overrides[api_get_db] = lambda: self.conn
        self.client = TestClient(app)

    def teardown_method(self):
        from src.main import app
        from src.api.deps import get_db as api_get_db
        app.dependency_overrides.pop(api_get_db, None)
        self.conn.close()

    def test_config_defaults_to_env_when_unset(self):
        """With no stored row, GET falls back to the DEV_MODE env default (False)."""
        with patch("src.config.settings.dev_mode", False):
            resp = self.client.get("/api/config")
        assert resp.status_code == 200
        assert resp.json()["dev_mode"] is False

    def test_put_toggles_and_persists(self):
        """PUT writes app_settings; subsequent GET reflects it regardless of env."""
        put = self.client.put("/api/config", json={"dev_mode": True})
        assert put.status_code == 200
        assert put.json()["dev_mode"] is True

        # Even with the env default False, the stored value wins.
        with patch("src.config.settings.dev_mode", False):
            assert self.client.get("/api/config").json()["dev_mode"] is True

        self.client.put("/api/config", json={"dev_mode": False})
        assert self.client.get("/api/config").json()["dev_mode"] is False

    def test_stored_value_overrides_env(self):
        from src.db.queries.app_settings import set_dev_mode
        set_dev_mode(self.conn, True)
        self.conn.commit()
        with patch("src.config.settings.dev_mode", False):
            assert self.client.get("/api/config").json()["dev_mode"] is True


# ---------------------------------------------------------------------------
# Stage-1 person-history gate
# ---------------------------------------------------------------------------

class TestPersonHistoryGate:
    """The known-person rule keeps 0.95 only when the person's own history does
    not contradict Transfers; an established non-Transfers prior routes to review."""

    def _conn_with_history(self, category: str, n: int):
        conn = _make_conn()
        _insert_statement(conn)
        for i in range(n):
            txn_id = f"hist{i}"
            conn.execute(
                """INSERT INTO transactions
                   (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key)
                   VALUES (?, 'stmt_01', ?, 100.0, 'debit', ?, 'SANYA PRASHANT')""",
                (txn_id, f"2026-01-{i+1:02d}", "UPI/SANYA PRASHANT/1234567890/UPI"),
            )
            conn.execute(
                """INSERT INTO annotations (id, transaction_id, category, confidence, source)
                   VALUES (?, ?, ?, 1.0, 'manual')""",
                (f"ann{i}", txn_id, category),
            )
        conn.commit()
        return conn

    def _txn(self):
        return {"id": "t_new", "raw_description": "UPI/SANYA PRASHANT/9999999999/UPI",
                "upi_meta": json.dumps({"vpa": "sanya@okhdfc", "ref": "9", "note": ""}),
                "txn_date": "2026-02-01", "amount": 500.0, "debit_credit": "debit"}

    def _people(self):
        return [("Sanya", "sanya@okhdfc")]

    def test_non_transfers_history_routes_to_review(self):
        from src.config import settings
        from src.pipeline.annotate import rule_annotation
        conn = self._conn_with_history("Entertainment", 4)
        result, trace = rule_annotation(conn, self._txn(), self._people())
        assert result is not None
        assert result.category == "Transfers"  # label never changed, only routing
        assert result.confidence <= settings.rag_defer_confidence_cap
        assert "person_history_disagrees" in trace.caps_applied

    def test_transfers_history_keeps_fast_path(self):
        from src.pipeline.annotate import rule_annotation
        conn = self._conn_with_history("Transfers", 4)
        result, trace = rule_annotation(conn, self._txn(), self._people())
        assert result.confidence == 0.95
        assert trace.caps_applied == []

    def test_cold_start_keeps_fast_path(self):
        from src.pipeline.annotate import rule_annotation
        conn = _make_conn()
        _insert_statement(conn)
        result, trace = rule_annotation(conn, self._txn(), self._people())
        assert result.confidence == 0.95

    def test_keyword_rule_not_gated(self):
        from src.pipeline.annotate import rule_annotation
        conn = self._conn_with_history("Entertainment", 4)
        txn = {"id": "t_kw", "raw_description": "Swiggy food order", "upi_meta": None,
               "txn_date": "2026-02-01", "amount": 300.0, "debit_credit": "debit"}
        result, trace = rule_annotation(conn, txn, [])
        assert result is not None
        assert result.confidence == 0.95


class TestPersonTokenMatching:
    """_person_token_matches: explicit token semantics (VPA vs name fragment)."""

    def _m(self, token, vpa=None, key=None, desc=""):
        from src.pipeline.annotate import _person_token_matches
        return _person_token_matches(token, vpa, key, desc)

    def test_name_token_prefix_matches_counterparty_word(self):
        assert self._m("karabi", key="KARABI BORA")
        assert self._m("sanya", key="SANYA PRASHANT")

    def test_name_token_matches_second_word_prefix(self):
        assert self._m("bora", key="KARABI BORA")

    def test_name_token_does_not_substring_match_merchant(self):
        # old behavior: 'sanya' in 'upi/vasanya foods/...' would fire
        assert not self._m("sanya", key="VASANYA FOODS", desc="upi/vasanya foods/123/upi")

    def test_short_token_no_longer_fires_mid_word(self):
        assert not self._m("ma", key="AABID ALI SO NA", desc="upi/aabid ali so na/1/salaman")
        assert self._m("ma", key="MA KITCHEN")  # still a legitimate word prefix

    def test_vpa_token_exact_only_when_vpa_present(self):
        assert self._m("rahul@okaxis", vpa="rahul@okaxis")
        assert not self._m("rahul@okaxis", vpa="notrahul@okaxis")

    def test_vpa_token_falls_back_to_desc_substring_without_vpa(self):
        assert self._m("rahul@okaxis", desc="imps rahul@okaxis transfer")

    def test_name_token_word_boundary_fallback_without_key(self):
        assert self._m("karabi", desc="imps to karabi bora ref 12")
        assert not self._m("karabi", desc="imps to notkarabi ref 12")


# ---------------------------------------------------------------------------
# Learned merchant memory (stage 1.5)
# ---------------------------------------------------------------------------

class TestLearnedRules:
    def _conn(self):
        conn = _make_conn()
        _insert_statement(conn)
        return conn

    def _add(self, conn, txn_id, key, category, source="manual", subcategory=None,
             merchant=None, tags="", txn_date="2026-01-10", annotated_at="2026-01-10"):
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
            "VALUES (?, 'stmt_01', ?, 100.0, 'debit', ?, ?)",
            (txn_id, txn_date, f"UPI/{key}/1/UPI", key),
        )
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, subcategory, merchant, tags, confidence, source, annotated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1.0, ?, ?)",
            (f"ann_{txn_id}", txn_id, category, subcategory, merchant, tags, source, annotated_at),
        )
        conn.commit()

    def test_promotes_at_support_and_purity(self):
        from src.db.queries.learned_rules import lookup_learned_rule
        conn = self._conn()
        for i in range(3):
            self._add(conn, f"s{i}", "SWIGGY", "Food & Dining", subcategory="Delivery", merchant="Swiggy")
        rule = lookup_learned_rule(conn, "SWIGGY")
        assert rule is not None
        assert rule.category == "Food & Dining"
        assert rule.subcategory == "Delivery"
        assert rule.merchant == "Swiggy"
        assert rule.support == 3 and rule.total == 3 and rule.purity == 1.0

    def test_suppressed_rule_not_returned(self):
        from src.db.queries.learned_rules import (
            lookup_learned_rule, list_learned_rules, suppress_learned_rule, restore_learned_rule,
        )
        conn = self._conn()
        for i in range(3):
            self._add(conn, f"s{i}", "SWIGGY", "Food & Dining", merchant="Swiggy")
        assert lookup_learned_rule(conn, "SWIGGY") is not None

        suppress_learned_rule(conn, "SWIGGY")
        conn.commit()
        assert lookup_learned_rule(conn, "SWIGGY") is None
        assert all(r.counterparty_key != "SWIGGY" for r in list_learned_rules(conn))

        # Idempotent, and restore brings it back.
        suppress_learned_rule(conn, "SWIGGY")
        assert restore_learned_rule(conn, "SWIGGY") is True
        assert restore_learned_rule(conn, "SWIGGY") is False
        conn.commit()
        assert lookup_learned_rule(conn, "SWIGGY") is not None

    def test_below_support_not_promoted(self):
        from src.db.queries.learned_rules import lookup_learned_rule
        conn = self._conn()
        self._add(conn, "s0", "SWIGGY", "Food & Dining")
        self._add(conn, "s1", "SWIGGY", "Food & Dining")
        assert lookup_learned_rule(conn, "SWIGGY") is None  # only 2 < min_support 3

    def test_low_purity_not_promoted(self):
        from src.db.queries.learned_rules import lookup_learned_rule
        conn = self._conn()
        # 3 Food, 2 Shopping → purity 0.6 < 0.9
        for i in range(3):
            self._add(conn, f"f{i}", "MIXED", "Food & Dining")
        for i in range(2):
            self._add(conn, f"sh{i}", "MIXED", "Shopping")
        assert lookup_learned_rule(conn, "MIXED") is None

    def test_machine_labels_do_not_promote(self):
        from src.db.queries.learned_rules import lookup_learned_rule
        conn = self._conn()
        for i in range(4):
            self._add(conn, f"m{i}", "BOTMERCH", "Shopping", source="llm")
        assert lookup_learned_rule(conn, "BOTMERCH") is None  # no verified labels

    def test_causal_cutoff_excludes_future_labels(self):
        from src.db.queries.learned_rules import lookup_learned_rule
        conn = self._conn()
        for i in range(3):
            self._add(conn, f"c{i}", "SWIGGY", "Food & Dining", txn_date=f"2026-02-0{i+1}")
        # Scoring a txn dated before the history sees nothing.
        assert lookup_learned_rule(conn, "SWIGGY", before_txn_date="2026-01-15") is None
        # Dated after, it fires.
        assert lookup_learned_rule(conn, "SWIGGY", before_txn_date="2026-03-01") is not None

    def test_excludes_self(self):
        from src.db.queries.learned_rules import lookup_learned_rule
        conn = self._conn()
        for i in range(3):
            self._add(conn, f"e{i}", "SWIGGY", "Food & Dining")
        # Excluding one of the 3 drops support to 2 → not established.
        assert lookup_learned_rule(conn, "SWIGGY", exclude_transaction_id="e0") is None

    def test_pipeline_stage_applies_when_enabled(self, monkeypatch):
        from src.config import settings
        from src.pipeline.annotate import rule_annotation
        monkeypatch.setattr(settings, "learned_rule_enabled", True)
        conn = self._conn()
        for i in range(3):
            self._add(conn, f"s{i}", "LICIOUS", "Food & Dining", merchant="Licious",
                      txn_date="2026-01-05")
        txn = {"id": "new", "raw_description": "UPI/LICIOUS/9/UPI", "counterparty_key": "LICIOUS",
               "upi_meta": None, "txn_date": "2026-02-01", "amount": 400.0, "debit_credit": "debit"}
        result, trace = rule_annotation(conn, txn, [])
        assert result is not None
        assert result.source == "learned_rule"
        assert result.category == "Food & Dining"
        assert trace.stage == "learned_rule"

    def test_pipeline_stage_inert_when_disabled(self, monkeypatch):
        from src.config import settings
        from src.pipeline.annotate import rule_annotation
        monkeypatch.setattr(settings, "learned_rule_enabled", False)
        conn = self._conn()
        for i in range(3):
            self._add(conn, f"s{i}", "LICIOUS", "Food & Dining")
        txn = {"id": "new", "raw_description": "UPI/LICIOUS/9/UPI", "counterparty_key": "LICIOUS",
               "upi_meta": None, "txn_date": "2026-02-01", "amount": 400.0, "debit_credit": "debit"}
        result, trace = rule_annotation(conn, txn, [])
        assert result is None  # falls through to RAG

    def test_person_rule_takes_priority(self, monkeypatch):
        from src.config import settings
        from src.pipeline.annotate import rule_annotation
        monkeypatch.setattr(settings, "learned_rule_enabled", True)
        conn = self._conn()
        # Even if a merchant-style history exists, a known person wins stage 1.
        for i in range(3):
            self._add(conn, f"s{i}", "KARABI BORA", "Food & Dining")
        txn = {"id": "new", "raw_description": "UPI/KARABI BORA/9/UPI",
               "counterparty_key": "KARABI BORA", "upi_meta": None,
               "txn_date": "2026-02-01", "amount": 100.0, "debit_credit": "debit"}
        result, _ = rule_annotation(conn, txn, [("ma", "karabi")])
        assert result.source == "rule"
        assert result.subcategory == "Peer Transfer"
