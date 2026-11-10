"""LLM call robustness: transport retries/timeouts and the per-call deadline,
the run-level circuit breakers, the job heartbeat, and never holding the
SQLite write lock across a slow model call."""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from finance_copilot.config import settings
from finance_copilot.pipeline.llm import (
    AnnotationResponse,
    LLMRequestRejected,
    LLMUnavailableError,
    annotate_transaction_llm,
    call_budget_s,
)
from finance_copilot.tests.test_annotation_pipeline import (
    _insert_statement,
    _insert_txn,
    _make_conn,
)

_URL = "http://localhost:11434/api/chat"
_TXN = {"id": "t1", "raw_description": "mystery shop", "upi_meta": None,
        "amount": 100, "debit_credit": "debit", "txn_date": "2026-01-15"}
_CATS = ["Shopping"]
_ANNOTATE = "finance_copilot.pipeline.annotate"


def _resp(status: int, body=None, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status, json={} if body is None else body, headers=headers, request=httpx.Request("POST", _URL)
    )


def _ok() -> httpx.Response:
    return _resp(200, {"message": {"content": json.dumps({"category": "Shopping", "confidence": 0.8})}})


@pytest.fixture(autouse=True)
def _ollama(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_url", "http://localhost:11434")
    monkeypatch.setattr(settings, "llm_max_attempts", 3)
    monkeypatch.setattr(settings, "llm_timeout_s", 60.0)


class TestLLMTransportRetries:
    def test_transient_connect_errors_are_retried_then_succeed(self):
        side = [httpx.ConnectError("refused"), httpx.ConnectError("refused"), _ok()]
        with patch("httpx.post", side_effect=side) as p, patch("time.sleep") as sleep:
            result = annotate_transaction_llm(_TXN, _CATS)
        assert result is not None and result.category == "Shopping"
        assert p.call_count == 3
        # Jittered exponential backoff between attempts, bounded.
        waits = [c.args[0] for c in sleep.call_args_list]
        assert len(waits) == 2 and all(0 < w <= 8 for w in waits)

    def test_read_timeout_is_retried_then_succeeds(self):
        with patch("httpx.post", side_effect=[httpx.ReadTimeout("slow"), _ok()]) as p, patch("time.sleep"):
            assert annotate_transaction_llm(_TXN, _CATS) is not None
        assert p.call_count == 2

    @pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504])
    def test_retryable_status_is_retried(self, status):
        with patch("httpx.post", side_effect=[_resp(status), _ok()]) as p, patch("time.sleep"):
            assert annotate_transaction_llm(_TXN, _CATS) is not None
        assert p.call_count == 2

    def test_retry_after_header_is_honoured_and_capped(self):
        side = [_resp(429, headers={"Retry-After": "2"}), _resp(503, headers={"Retry-After": "999"}), _ok()]
        with patch("httpx.post", side_effect=side), patch("time.sleep") as sleep:
            assert annotate_transaction_llm(_TXN, _CATS) is not None
        assert [c.args[0] for c in sleep.call_args_list] == [2.0, 30.0]

    @pytest.mark.parametrize("value", ["nan", "inf", "Wed, 21 Oct 2026 07:28:00 GMT"])
    def test_unusable_retry_after_falls_back_to_backoff(self, value):
        side = [_resp(503, headers={"Retry-After": value}), _ok()]
        with patch("httpx.post", side_effect=side), patch("time.sleep") as sleep:
            assert annotate_transaction_llm(_TXN, _CATS) is not None
        (wait,) = [c.args[0] for c in sleep.call_args_list]
        assert 0 < wait <= 8

    @pytest.mark.parametrize("status", [401, 403, 404])
    def test_setup_error_fails_fast_as_unavailable(self, status):
        body = {"error": "model 'qwen9' not found"}
        with patch("httpx.post", return_value=_resp(status, body)) as p, patch("time.sleep") as sleep, \
             pytest.raises(LLMUnavailableError, match=rf"refused the request \(HTTP {status}: model"):
            annotate_transaction_llm(_TXN, _CATS)
        assert p.call_count == 1
        sleep.assert_not_called()

    @pytest.mark.parametrize("status", [400, 413, 422])
    def test_request_error_rejects_only_this_request(self, status):
        """A 400 for one prompt (e.g. over the context window on a BYOM endpoint)
        is about that row: no retry, no re-ask, and not an outage."""
        body = {"error": {"message": "context_length_exceeded"}}
        with patch("httpx.post", return_value=_resp(status, body)) as p, patch("time.sleep") as sleep, \
             pytest.raises(LLMRequestRejected, match=rf"HTTP {status}: context_length_exceeded"):
            annotate_transaction_llm(_TXN, _CATS)
        assert p.call_count == 1
        sleep.assert_not_called()

    def test_persistent_outage_gives_up_after_max_attempts(self):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")) as p, patch("time.sleep"), \
             pytest.raises(LLMUnavailableError, match=r"after 3 attempt\(s\)"):
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
        assert 41.0 < timeout.read <= 42.0

    def test_malformed_url_is_unavailable_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(settings, "ollama_url", "http://[::1")
        with pytest.raises(LLMUnavailableError, match="InvalidURL"):
            annotate_transaction_llm(_TXN, _CATS)

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

    def test_local_model_error_on_one_request_is_a_rejection(self, monkeypatch):
        from finance_copilot import local_llm

        monkeypatch.setattr(settings, "llm_provider", "local")
        err = ValueError("Requested tokens exceed context window")
        with patch.object(local_llm, "chat_completion", side_effect=err), \
             pytest.raises(LLMRequestRejected, match="context window"):
            annotate_transaction_llm(_TXN, _CATS)


class TestBadAnswers:
    def test_unparseable_answer_is_per_transaction_not_an_outage(self):
        """Bad output after the self-repair re-asks returns None (this row
        failed) rather than raising (provider down): the run keeps going."""
        bad = _resp(200, {"message": {"content": "not json"}})
        with patch("httpx.post", return_value=bad) as p, patch("time.sleep"):
            assert annotate_transaction_llm(_TXN, _CATS, max_retries=2) is None
        assert p.call_count == 3  # one ask + two repair re-asks, no transport retries

    @pytest.mark.parametrize("body", [
        {"message": None},
        {"message": {"content": {"category": "Shopping"}}},
        {"choices": []},
        [],
        "text",
    ])
    def test_unexpected_response_shape_is_a_bad_answer_not_a_crash(self, body):
        with patch("httpx.post", return_value=_resp(200, body)), patch("time.sleep"):
            assert annotate_transaction_llm(_TXN, _CATS, max_retries=0) is None

    def test_null_openai_content_is_a_bad_answer(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "llm_base_url", "https://llm.example.com/v1")
        body = {"choices": [{"message": {"content": None}}]}
        with patch("httpx.post", return_value=_resp(200, body)), patch("time.sleep"):
            assert annotate_transaction_llm(_TXN, _CATS, max_retries=0) is None


class TestCallDeadline:
    def test_budget_is_attempts_times_timeout(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_timeout_s", 20.0)
        monkeypatch.setattr(settings, "llm_max_attempts", 4)
        assert call_budget_s() == 80.0

    def test_no_backoff_sleep_past_the_deadline(self):
        """With the budget nearly spent, a transient failure gives up instead of
        sleeping into (and past) the deadline."""
        with patch("finance_copilot.pipeline.llm.call_budget_s", return_value=0.3), \
             patch("httpx.post", side_effect=httpx.ConnectError("refused")) as p:
            t0 = time.monotonic()
            with pytest.raises(LLMUnavailableError, match=r"after 1 attempt\(s\)"):
                annotate_transaction_llm(_TXN, _CATS)
        assert p.call_count == 1  # backoff (>=1s) would overrun a 0.3s budget
        assert time.monotonic() - t0 < 0.5

    def test_re_asks_share_the_deadline(self):
        """The self-repair loop can't multiply the budget: once it's spent,
        no further re-ask is sent."""
        def slow_bad(*_a, **_k):
            time.sleep(0.15)
            return _resp(200, {"message": {"content": "not json"}})

        with patch("finance_copilot.pipeline.llm.call_budget_s", return_value=0.1), \
             patch("httpx.post", side_effect=slow_bad) as p:
            assert annotate_transaction_llm(_TXN, _CATS, max_retries=2) is None
        assert p.call_count == 1

    def test_read_timeout_never_exceeds_remaining_budget(self):
        with patch("finance_copilot.pipeline.llm.call_budget_s", return_value=7.0), \
             patch("httpx.post", return_value=_ok()) as p:
            annotate_transaction_llm(_TXN, _CATS)
        timeout = p.call_args.kwargs["timeout"]
        assert timeout.read <= 7.0
        assert timeout.connect <= 5.0

    def test_stale_window_covers_one_row_budget(self):
        """The job reaper must never see a live worker as dead: one row is at
        most an embedding call (60s read) plus one capped LLM call."""
        from finance_copilot.api.routes.annotations import STALE_JOB_MINUTES

        worst_row_s = 60 + 5 + call_budget_s()
        assert worst_row_s * 2 < STALE_JOB_MINUTES * 60


class TestRunGuard:
    def test_unavailable_trips_at_once(self):
        from finance_copilot.pipeline.annotate import RunGuard

        guard = RunGuard()
        fn = MagicMock(side_effect=LLMUnavailableError("down"))
        assert guard.llm(fn) is None
        assert guard.llm(fn) is None
        assert fn.call_count == 1
        assert guard.llm_error == "down"

    def test_consecutive_rejections_trip_and_success_resets(self):
        from finance_copilot.pipeline.annotate import RunGuard

        guard = RunGuard()
        rejected = LLMRequestRejected("HTTP 400")
        fn = MagicMock(side_effect=[rejected, rejected, "ok", rejected, rejected, rejected, "unreached"])
        results = [guard.llm(fn) for _ in range(7)]
        assert results == [None, None, "ok", None, None, None, None]
        assert fn.call_count == 6
        assert "3 requests in a row were rejected" in guard.llm_error

    def test_embedding_failure_trips_embeddings_only(self):
        from finance_copilot.pipeline.annotate import RunGuard
        from finance_copilot.pipeline.embed import EmbeddingsUnavailable

        guard = RunGuard()
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=httpx.ConnectError("refused")) as emb:
            with pytest.raises(httpx.ConnectError):
                guard.embed("a")
            with pytest.raises(EmbeddingsUnavailable, match="disabled for this run"):
                guard.embed("b")
        assert emb.call_count == 1
        assert guard.llm_error is None


class TestCircuitBreakerInPipeline:
    def setup_method(self):
        self.conn = _make_conn()
        _insert_statement(self.conn)

    def teardown_method(self):
        self.conn.close()

    def _unknown_rows(self, n: int) -> None:
        for i in range(n):
            _insert_txn(self.conn, f"u{i}", f"zqx unknown vendor {i}")

    def test_outage_stops_llm_calls_for_rest_of_run(self):
        from finance_copilot.pipeline.annotate import auto_annotate

        self._unknown_rows(3)
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=Exception("no embedder")), \
             patch(f"{_ANNOTATE}.annotate_transaction_llm",
                   side_effect=LLMUnavailableError("can't reach the provider")) as llm:
            result = auto_annotate(self.conn)

        assert llm.call_count == 1
        assert result.llm_failed == 3
        assert result.llm_annotated == 0
        assert result.llm_error == "can't reach the provider"
        assert self.conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 0

    def test_outage_in_rag_stage_stops_rag_prompted_and_plain_llm(self):
        from finance_copilot.pipeline import annotate

        self._unknown_rows(3)
        rag_llm = MagicMock(side_effect=LLMUnavailableError("HTTP 503"))

        def fake_rag(conn, txn, category_list, ctx=None, *, as_of, guard=None, **_):
            guard.llm(rag_llm, txn)
            return None, None

        with patch.object(annotate, "_try_rag_annotation", side_effect=fake_rag), \
             patch.object(annotate, "annotate_transaction_llm") as plain_llm:
            result = annotate.auto_annotate(self.conn)

        assert rag_llm.call_count == 1
        plain_llm.assert_not_called()
        assert result.llm_failed == 3
        assert result.llm_error == "HTTP 503"

    def test_repeated_rejections_trip_the_breaker(self):
        from finance_copilot.pipeline.annotate import auto_annotate

        self._unknown_rows(5)
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=Exception("no embedder")), \
             patch(f"{_ANNOTATE}.annotate_transaction_llm",
                   side_effect=LLMRequestRejected("provider rejected this request (HTTP 400)")) as llm:
            result = auto_annotate(self.conn)

        assert llm.call_count == 3
        assert result.llm_failed == 5
        assert "3 requests in a row were rejected" in result.llm_error

    def test_embedding_outage_is_paid_once_per_run(self):
        from finance_copilot.pipeline.annotate import auto_annotate

        self._unknown_rows(4)
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=httpx.ReadTimeout("hung")) as emb, \
             patch(f"{_ANNOTATE}.annotate_transaction_llm",
                   return_value=AnnotationResponse(category="Shopping", confidence=0.7)) as llm:
            result = auto_annotate(self.conn)

        assert emb.call_count == 1
        assert llm.call_count == 4  # plain LLM still serves every row
        assert result.llm_annotated == 4
        assert result.llm_error is None

    def test_healthy_llm_run_reports_no_error(self):
        from finance_copilot.pipeline.annotate import auto_annotate

        self._unknown_rows(2)
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=Exception("no embedder")), \
             patch(f"{_ANNOTATE}.annotate_transaction_llm",
                   return_value=AnnotationResponse(category="Shopping", confidence=0.7)) as llm:
            result = auto_annotate(self.conn)

        assert llm.call_count == 2
        assert result.llm_annotated == 2
        assert result.llm_error is None


class TestHeartbeat:
    def test_rows_falling_through_rag_still_heartbeat(self):
        """A row whose embedding/LLM call fails isn't processed yet, but the job
        must still look alive to the reaper while such rows pile up."""
        from finance_copilot.pipeline.annotate import auto_annotate

        conn = _make_conn()
        _insert_statement(conn)
        for i in range(3):
            _insert_txn(conn, f"u{i}", f"zqx unknown vendor {i}")
        calls = []

        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=Exception("no embedder")), \
             patch(f"{_ANNOTATE}.annotate_transaction_llm", return_value=None):
            auto_annotate(conn, progress_cb=lambda p, t: calls.append((p, t)))

        # One heartbeat per RAG fall-through (nothing processed yet), then one
        # tick per row in the plain-LLM stage.
        assert calls == [(0, 3), (0, 3), (0, 3), (1, 3), (2, 3), (3, 3)]
        conn.close()


class TestNoWriteLockAcrossSlowCalls:
    def test_pending_writes_are_committed_before_embedding_and_llm(self):
        """A rule match leaves an uncommitted insert (batched commits); it must be
        flushed before the slow embedding/LLM calls, or every other writer waits
        out busy_timeout behind the model."""
        from finance_copilot.pipeline.annotate import auto_annotate

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

        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=embed), \
             patch(f"{_ANNOTATE}.annotate_transaction_llm", side_effect=llm):
            result = auto_annotate(conn)

        assert in_txn_during == {"embed": False, "llm": False}
        assert result.rule_matched == 1 and result.llm_annotated == 1
        conn.close()

    def test_plain_llm_stage_commits_each_label_before_the_next_call(self):
        """Within the plain-LLM stage, the label persisted for one row must not
        sit uncommitted through the next row's LLM call."""
        from finance_copilot.pipeline.annotate import auto_annotate

        conn = _make_conn()
        _insert_statement(conn)
        for i in range(3):
            _insert_txn(conn, f"u{i}", f"zqx unknown vendor {i}")
        seen = []

        def llm(_txn, _cats):
            seen.append(conn.in_transaction)
            return AnnotationResponse(category="Shopping", confidence=0.7)

        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=Exception("no embedder")), \
             patch(f"{_ANNOTATE}.annotate_transaction_llm", side_effect=llm):
            result = auto_annotate(conn)

        assert seen == [False, False, False]
        assert result.llm_annotated == 3
        conn.close()
