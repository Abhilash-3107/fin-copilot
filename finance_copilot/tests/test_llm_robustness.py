"""LLM call robustness: transport retries/timeouts, the run-level circuit
breaker, and never holding the SQLite write lock across a slow model call."""
from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from finance_copilot.config import settings
from finance_copilot.pipeline.llm import LLMUnavailableError, annotate_transaction_llm
from finance_copilot.tests.test_annotation_pipeline import (
    _insert_statement,
    _insert_txn,
    _make_conn,
)

_URL = "http://localhost:11434/api/chat"
_TXN = {"id": "t1", "raw_description": "mystery shop", "upi_meta": None,
        "amount": 100, "debit_credit": "debit", "txn_date": "2026-01-15"}
_CATS = ["Shopping"]


def _resp(status: int, body: dict | None = None, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body or {}, headers=headers, request=httpx.Request("POST", _URL))


def _ok() -> httpx.Response:
    content = json.dumps({"category": "Shopping", "confidence": 0.8})
    return _resp(200, {"message": {"content": content}})


@pytest.fixture(autouse=True)
def _ollama(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_max_attempts", 3)
    monkeypatch.setattr(settings, "llm_timeout_s", 60.0)


class TestLLMTransportRetries:
    def test_transient_connect_errors_are_retried_then_succeed(self):
        post = patch("httpx.post", side_effect=[httpx.ConnectError("refused"), httpx.ConnectError("refused"), _ok()])
        with post as p, patch("time.sleep") as sleep:
            result = annotate_transaction_llm(_TXN, _CATS)
        assert result is not None and result.category == "Shopping"
        assert p.call_count == 3
        # Jittered exponential backoff between attempts, bounded.
        waits = [c.args[0] for c in sleep.call_args_list]
        assert len(waits) == 2 and all(0 < w <= 8 for w in waits)

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_retryable_status_is_retried(self, status):
        with patch("httpx.post", side_effect=[_resp(status), _ok()]) as p, patch("time.sleep"):
            assert annotate_transaction_llm(_TXN, _CATS) is not None
        assert p.call_count == 2

    def test_retry_after_header_is_honoured_and_capped(self):
        side = [_resp(429, headers={"Retry-After": "2"}), _resp(503, headers={"Retry-After": "999"}), _ok()]
        with patch("httpx.post", side_effect=side), patch("time.sleep") as sleep:
            assert annotate_transaction_llm(_TXN, _CATS) is not None
        assert [c.args[0] for c in sleep.call_args_list] == [2.0, 30.0]

    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    def test_client_error_fails_fast_without_retry(self, status):
        body = {"error": "model 'qwen9' not found"}
        with patch("httpx.post", return_value=_resp(status, body)) as p, patch("time.sleep") as sleep, \
             pytest.raises(LLMUnavailableError, match=rf"rejected the request \(HTTP {status}: model"):
            annotate_transaction_llm(_TXN, _CATS)
        assert p.call_count == 1
        sleep.assert_not_called()

    def test_persistent_outage_gives_up_after_max_attempts(self):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")) as p, patch("time.sleep"), \
             pytest.raises(LLMUnavailableError, match="after 3 attempts"):
            annotate_transaction_llm(_TXN, _CATS)
        assert p.call_count == settings.llm_max_attempts

    def test_persistent_5xx_gives_up_after_max_attempts(self):
        with patch("httpx.post", return_value=_resp(503)) as p, patch("time.sleep"), \
             pytest.raises(LLMUnavailableError, match="HTTP 503"):
            annotate_transaction_llm(_TXN, _CATS)
        assert p.call_count == 3

    def test_split_timeout_short_connect_long_read(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_timeout_s", 42.0)
        with patch("httpx.post", return_value=_ok()) as p:
            annotate_transaction_llm(_TXN, _CATS)
        timeout = p.call_args.kwargs["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.connect == 5.0
        assert timeout.read == 42.0

    def test_bad_answer_is_per_transaction_not_an_outage(self):
        """Unparseable output after the self-repair re-asks returns None (this row
        failed) rather than raising (provider down): the run keeps going."""
        bad = _resp(200, {"message": {"content": "not json"}})
        with patch("httpx.post", return_value=bad) as p, patch("time.sleep"):
            assert annotate_transaction_llm(_TXN, _CATS, max_retries=2) is None
        assert p.call_count == 3  # one ask + two repair re-asks, no transport retries

    def test_insecure_key_transport_is_unavailable_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "llm_base_url", "http://llm.example.com/v1")
        monkeypatch.setattr(settings, "llm_api_key", "sk-test")
        with patch("httpx.post") as p, pytest.raises(LLMUnavailableError, match="cleartext"):
            annotate_transaction_llm(_TXN, _CATS)
        p.assert_not_called()

    def test_local_model_missing_is_unavailable(self, monkeypatch):
        from finance_copilot import local_llm

        monkeypatch.setattr(settings, "llm_provider", "local")
        with patch.object(local_llm, "chat_completion", side_effect=local_llm.LocalLLMError("not downloaded")), \
             pytest.raises(LLMUnavailableError, match="not downloaded"):
            annotate_transaction_llm(_TXN, _CATS)


class TestCircuitBreaker:
    def setup_method(self):
        self.conn = _make_conn()
        _insert_statement(self.conn)

    def teardown_method(self):
        self.conn.close()

    def test_outage_stops_llm_calls_for_rest_of_run(self):
        from finance_copilot.pipeline.annotate import auto_annotate

        for i in range(3):
            _insert_txn(self.conn, f"u{i}", f"zqx unknown vendor {i}")

        with patch("finance_copilot.pipeline.annotate.get_embedding_single", side_effect=Exception("no embedder")), \
             patch("finance_copilot.pipeline.annotate.annotate_transaction_llm",
                   side_effect=LLMUnavailableError("can't reach the provider")) as llm:
            result = auto_annotate(self.conn)

        assert llm.call_count == 1
        assert result.llm_failed == 3
        assert result.llm_annotated == 0
        assert result.llm_error == "can't reach the provider"
        n = self.conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
        assert n == 0  # left unlabelled for the next run, not guessed

    def test_outage_in_rag_stage_disables_rag_prompted_and_plain_llm(self):
        from finance_copilot.pipeline import annotate

        for i in range(3):
            _insert_txn(self.conn, f"u{i}", f"zqx unknown vendor {i}")

        allow_llm_seen = []

        def fake_rag(conn, txn, category_list, ctx=None, *, as_of, allow_llm=True, **_):
            allow_llm_seen.append(allow_llm)
            if allow_llm:
                raise LLMUnavailableError("HTTP 503")
            return None, None

        with patch.object(annotate, "_try_rag_annotation", side_effect=fake_rag), \
             patch.object(annotate, "annotate_transaction_llm") as llm:
            result = annotate.auto_annotate(self.conn)

        assert allow_llm_seen == [True, False, False]
        llm.assert_not_called()
        assert result.llm_failed == 3
        assert result.llm_error == "HTTP 503"

    def test_healthy_run_reports_no_llm_error(self):
        from finance_copilot.pipeline.annotate import auto_annotate

        _insert_txn(self.conn, "t1", "Swiggy food order")
        assert auto_annotate(self.conn).llm_error is None


class TestNoWriteLockAcrossSlowCalls:
    def test_pending_writes_are_committed_before_embedding_and_llm(self):
        """A rule match leaves an uncommitted insert (batched commits); it must be
        flushed before the slow embedding/LLM calls, or every other writer waits
        out busy_timeout behind the model."""
        from finance_copilot.pipeline.annotate import auto_annotate
        from finance_copilot.pipeline.llm import AnnotationResponse

        conn = _make_conn()
        _insert_statement(conn)
        _insert_txn(conn, "r1", "Swiggy food order")
        _insert_txn(conn, "u1", "zqx unknown vendor")

        in_txn_during = {}

        def embed(_text):
            in_txn_during["embed"] = conn.in_transaction
            raise Exception("no embedder")

        def llm(_txn, _cats):
            in_txn_during["llm"] = conn.in_transaction
            return AnnotationResponse(category="Shopping", confidence=0.7)

        with patch("finance_copilot.pipeline.annotate.get_embedding_single", side_effect=embed), \
             patch("finance_copilot.pipeline.annotate.annotate_transaction_llm", side_effect=llm):
            result = auto_annotate(conn)

        assert in_txn_during == {"embed": False, "llm": False}
        assert result.rule_matched == 1 and result.llm_annotated == 1
        conn.close()
