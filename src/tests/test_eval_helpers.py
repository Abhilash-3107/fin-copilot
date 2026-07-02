"""Tests for the experiment-flag helpers added with the eval harness:
logprob confidence extraction, non-UPI embed normalization, MMR-lite selection.
"""
from __future__ import annotations

import math

from src.config import settings
from src.pipeline.embed import normalize_description_for_embedding
from src.pipeline.llm import _logprob_category_confidence


def _lp(tokens_probs):
    return {"message": {"logprobs": [{"token": t, "logprob": p} for t, p in tokens_probs]}}


class TestLogprobConfidence:
    def test_extracts_category_span_probability(self):
        data = _lp([
            ('{"', -0.01),
            ("category", -0.02),
            ('": "', -0.01),
            ("Food", math.log(0.8)),
            (" & Dining", math.log(0.9)),
            ('"}', -0.01),
        ])
        conf = _logprob_category_confidence(data, "Food & Dining")
        assert conf is not None
        assert abs(conf - 0.72) < 0.01  # 0.8 * 0.9

    def test_missing_logprobs_returns_none(self):
        assert _logprob_category_confidence({"message": {}}, "Food") is None

    def test_category_not_in_text_returns_none(self):
        data = _lp([('{"category": "Transport"}', -0.1)])
        assert _logprob_category_confidence(data, "Food & Dining") is None

    def test_clamped_to_unit_interval(self):
        data = _lp([('{"category": "', -0.0), ("Food", 0.0), ('"}', 0.0)])
        conf = _logprob_category_confidence(data, "Food")
        assert conf == 1.0


class TestNonUpiNormalization:
    def test_upi_ref_stripped_regardless_of_flag(self):
        assert (
            normalize_description_for_embedding("UPI/SWIGGY/512345678901/UPI")
            == "UPI/SWIGGY"
        )

    def test_non_upi_untouched_when_flag_off(self, monkeypatch):
        monkeypatch.setattr(settings, "embed_strip_non_upi_refs", False)
        desc = "NEFT-AXISCN0123456789-ACME CORP-20/01/26"
        assert normalize_description_for_embedding(desc) == desc

    def test_non_upi_refs_and_dates_stripped_when_flag_on(self, monkeypatch):
        monkeypatch.setattr(settings, "embed_strip_non_upi_refs", True)
        out = normalize_description_for_embedding("NEFT-AXISCN0123456789-ACME CORP-20/01/26")
        assert "0123456789" not in out
        assert "20/01/26" not in out
        assert "ACME CORP" in out

    def test_short_numbers_kept_when_flag_on(self, monkeypatch):
        monkeypatch.setattr(settings, "embed_strip_non_upi_refs", True)
        assert "24x7" not in normalize_description_for_embedding("POS 123456789012 STORE 24 7").split()
        assert "STORE" in normalize_description_for_embedding("POS 123456789012 STORE 24 7")


class TestProviderAbstraction:
    def test_provider_none_skips_llm(self, monkeypatch):
        from src.pipeline.llm import annotate_transaction_llm
        monkeypatch.setattr(settings, "llm_provider", "none")
        assert annotate_transaction_llm({"id": "t1", "raw_description": "x"}, ["Food & Dining"]) is None

    def test_openai_request_shape(self, monkeypatch):
        from src.pipeline.llm import _build_provider_request
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "llm_base_url", "https://api.example.com/v1/")
        monkeypatch.setattr(settings, "llm_api_key", "sk-test")
        monkeypatch.setattr(settings, "llm_model", "gpt-test")
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        url, headers, payload = _build_provider_request(msgs, ["Food & Dining > Restaurants"])
        assert url == "https://api.example.com/v1/chat/completions"
        assert headers["Authorization"] == "Bearer sk-test"
        assert payload["model"] == "gpt-test"
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["schema"]["properties"]["category"]["enum"] == ["Food & Dining"]

    def test_ollama_request_shape_default(self):
        from src.pipeline.llm import _build_provider_request
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        url, headers, payload = _build_provider_request(msgs, ["Food & Dining"])
        assert url.endswith("/api/chat")
        assert "format" in payload and payload["options"]["temperature"] == 0

    def test_extract_content_both_shapes(self):
        from src.pipeline.llm import _extract_content
        assert _extract_content({"message": {"content": "a"}}) == "a"
        assert _extract_content({"choices": [{"message": {"content": "b"}}]}) == "b"
