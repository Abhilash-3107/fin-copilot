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

    def test_read_vs_connect_timeout(self):
        """Connected-but-slow fails the row; can't-even-connect is the provider."""
        from finance_copilot.pipeline.llm import LLMTimeout

        with patch("httpx.post", side_effect=httpx.ReadTimeout("slow")), patch("time.sleep"), \
             pytest.raises(LLMTimeout):
            annotate_transaction_llm(_TXN, _CATS)
        with patch("httpx.post", side_effect=httpx.ConnectTimeout("no route")), patch("time.sleep"), \
             pytest.raises(LLMUnavailableError, match="ConnectTimeout"):
            annotate_transaction_llm(_TXN, _CATS)

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

    def test_unsupported_scheme_is_not_retried(self, monkeypatch):
        monkeypatch.setattr(settings, "ollama_url", "ftp://localhost:11434")
        with patch("time.sleep") as sleep, pytest.raises(LLMUnavailableError, match="UnsupportedProtocol"):
            annotate_transaction_llm(_TXN, _CATS)
        sleep.assert_not_called()

    def test_embedding_call_has_short_connect_timeout(self, monkeypatch):
        from finance_copilot.pipeline.embed import get_embedding_single

        monkeypatch.setattr(settings, "embedding_provider", "ollama")
        ok = httpx.Response(200, json={"embeddings": [[0.1, 0.2]]}, request=httpx.Request("POST", _URL))
        with patch("httpx.post", return_value=ok) as p:
            assert get_embedding_single("x") == [0.1, 0.2]
        timeout = p.call_args.kwargs["timeout"]
        assert timeout.connect == 5.0 and timeout.read == 60.0


class TestRunGuard:
    def test_unavailable_trips_at_once(self):
        from finance_copilot.pipeline.annotate import RunGuard

        guard = RunGuard()
        fn = MagicMock(side_effect=LLMUnavailableError("down"))
        assert guard.llm(fn) is None
        assert guard.llm(fn) is None
        assert fn.call_count == 1
        assert guard.llm_error == "down"

    def test_consecutive_failures_of_any_kind_trip_and_success_resets(self):
        """Rejections, unparseable answers (None) and unexpected exceptions all
        count toward the same in-a-row limit; one good answer resets it."""
        from finance_copilot.pipeline.annotate import _MAX_CONSECUTIVE_LLM_FAILURES, RunGuard

        assert _MAX_CONSECUTIVE_LLM_FAILURES == 5
        guard = RunGuard()
        rejected = LLMRequestRejected("HTTP 400")
        weird = UnicodeEncodeError("ascii", "sk-​", 3, 4, "ordinal not in range")
        fn = MagicMock(side_effect=[
            rejected, None, weird, None, "ok",              # 4 failures, then reset
            rejected, None, weird, None, rejected,          # 5 in a row: trips
            "unreached",
        ])
        results = [guard.llm(fn) for _ in range(11)]
        assert results == [None] * 4 + ["ok"] + [None] * 6
        assert fn.call_count == 10
        assert guard.llm_error.startswith("5 requests in a row failed; last: HTTP 400")

    def test_unexpected_exception_never_escapes(self):
        from finance_copilot.pipeline.annotate import RunGuard

        guard = RunGuard()
        assert guard.llm(MagicMock(side_effect=AttributeError("'list' object has no attribute 'get'"))) is None
        assert guard.llm_error is None  # one failure is a row, not an outage

    def test_one_timeout_is_tolerated_two_in_a_row_trip(self):
        from finance_copilot.pipeline.annotate import _MAX_CONSECUTIVE_LLM_TIMEOUTS, RunGuard
        from finance_copilot.pipeline.llm import LLMTimeout

        assert _MAX_CONSECUTIVE_LLM_TIMEOUTS == 2
        guard = RunGuard()
        slow = LLMTimeout("no answer within the time limit")
        fn = MagicMock(side_effect=[slow, "ok", slow, None, slow, slow, "unreached"])
        results = [guard.llm(fn) for _ in range(7)]
        # slow / ok resets / slow / bad answer breaks the timeout streak /
        # slow / slow -> trips
        assert results == [None, "ok", None, None, None, None, None]
        assert fn.call_count == 6
        assert "timed out on 2 requests in a row" in guard.llm_error
        assert "LLM_TIMEOUT_S" in guard.llm_error  # the env var a user can actually set

    def test_timeouts_also_count_toward_the_failure_limit(self):
        """Alternating timeouts and bad answers never make 2 timeouts in a row,
        but they're still 5 failed rows in a row."""
        from finance_copilot.pipeline.annotate import RunGuard
        from finance_copilot.pipeline.llm import LLMTimeout

        guard = RunGuard()
        slow = LLMTimeout("no answer within the time limit")
        fn = MagicMock(side_effect=[slow, None, slow, None, slow, "unreached"])
        assert [guard.llm(fn) for _ in range(6)] == [None] * 6
        assert fn.call_count == 5
        assert guard.llm_error.startswith("5 requests in a row failed")

    def test_streaks_are_per_stage(self):
        """A failing few-shot stage switches off only itself; the plain prompt
        keeps its own streak and still runs."""
        from finance_copilot.pipeline.annotate import RunGuard

        guard = RunGuard()
        rejected = LLMRequestRejected("HTTP 400: context_length_exceeded")
        few_shot = MagicMock(side_effect=rejected)
        plain = MagicMock(return_value="ok")
        for _ in range(6):
            assert guard.llm(few_shot, stage="rag_prompted") is None
            assert guard.llm(plain) == "ok"
        assert few_shot.call_count == 5  # off after 5 in a row, for this stage only
        assert plain.call_count == 6
        assert guard.stages_off == {"rag_prompted"}
        assert guard.llm_error is None

    def test_unavailable_in_any_stage_switches_the_llm_off(self):
        from finance_copilot.pipeline.annotate import RunGuard

        guard = RunGuard()
        assert guard.llm(MagicMock(side_effect=LLMUnavailableError("down")), stage="rag_prompted") is None
        plain = MagicMock()
        assert guard.llm(plain) is None
        plain.assert_not_called()
        assert guard.llm_error == "down"

    def test_ai_off_never_calls_the_llm(self, monkeypatch):
        from finance_copilot.pipeline.annotate import RunGuard

        monkeypatch.setattr(settings, "llm_provider", "none")
        guard = RunGuard()
        fn = MagicMock()
        for _ in range(10):
            assert guard.llm(fn) is None
        fn.assert_not_called()
        assert guard.llm_error is None  # AI off is the normal no-AI state, not an outage

    @pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.InvalidURL("bad")])
    def test_embedding_connect_failure_switches_off_at_once(self, exc):
        from finance_copilot.pipeline.annotate import RunGuard
        from finance_copilot.pipeline.embed import EmbeddingsUnavailable

        guard = RunGuard()
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=exc) as emb:
            with pytest.raises(type(exc)):
                guard.embed("a")
            with pytest.raises(EmbeddingsUnavailable, match="off for this run"):
                guard.embed("b")
        assert emb.call_count == 1
        assert guard.embeddings_off and type(exc).__name__ in guard.embed_error
        assert guard.llm_error is None

    def test_one_embedding_timeout_is_tolerated_two_in_a_row_are_not(self):
        from finance_copilot.pipeline.annotate import RunGuard

        guard = RunGuard()
        timeout = httpx.ReadTimeout("slow")
        side = [timeout, [0.1], timeout, timeout, [0.2]]
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=side) as emb:
            outcomes = []
            for _ in range(5):
                try:
                    outcomes.append(guard.embed("x"))
                except Exception as e:
                    outcomes.append(type(e).__name__)
        assert outcomes == ["ReadTimeout", [0.1], "ReadTimeout", "ReadTimeout", "EmbeddingsUnavailable"]
        assert emb.call_count == 4
        assert "ReadTimeout" in guard.embed_error

    def test_embeddings_configured_off_is_not_an_error(self):
        from finance_copilot.pipeline.annotate import RunGuard
        from finance_copilot.pipeline.embed import EmbeddingsUnavailable

        guard = RunGuard()
        with patch(f"{_ANNOTATE}.get_embedding_single",
                   side_effect=EmbeddingsUnavailable("embedding_provider is 'none'")) as emb:
            for _ in range(3):
                with pytest.raises(EmbeddingsUnavailable):
                    guard.embed("x")
        assert emb.call_count == 1
        assert guard.embeddings_off and guard.embed_error is None


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

        self._unknown_rows(7)
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=Exception("no embedder")), \
             patch(f"{_ANNOTATE}.annotate_transaction_llm",
                   side_effect=LLMRequestRejected("provider rejected this request (HTTP 400)")) as llm:
            result = auto_annotate(self.conn)

        assert llm.call_count == 5
        assert result.llm_failed == 7
        assert "5 requests in a row failed" in result.llm_error

    def test_model_that_never_answers_in_json_trips_the_breaker(self):
        """A chatty BYOM model that ignores the schema returns 200s that never
        parse: that must not cost every row of a big import."""
        from finance_copilot.pipeline.annotate import auto_annotate

        self._unknown_rows(8)
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=Exception("no embedder")), \
             patch(f"{_ANNOTATE}.annotate_transaction_llm", return_value=None) as llm:
            result = auto_annotate(self.conn)

        assert llm.call_count == 5
        assert result.llm_failed == 8
        assert "aren't valid JSON" in result.llm_error

    def test_unexpected_llm_exception_does_not_fail_the_run(self):
        from finance_copilot.pipeline.annotate import auto_annotate

        self._unknown_rows(2)
        bad_key = UnicodeEncodeError("ascii", "sk-​", 3, 4, "ordinal not in range")
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=Exception("no embedder")), \
             patch(f"{_ANNOTATE}.annotate_transaction_llm", side_effect=bad_key):
            result = auto_annotate(self.conn)

        assert result.llm_failed == 2
        assert result.total_processed == 2

    def test_embedding_outage_is_paid_twice_per_run_at_most(self):
        from finance_copilot.pipeline.annotate import auto_annotate

        self._unknown_rows(4)
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=httpx.ReadTimeout("hung")) as emb, \
             patch(f"{_ANNOTATE}.annotate_transaction_llm",
                   return_value=AnnotationResponse(category="Shopping", confidence=0.7)) as llm:
            result = auto_annotate(self.conn)

        assert emb.call_count == 2
        assert llm.call_count == 4  # plain LLM still serves every row
        assert result.llm_annotated == 4
        assert result.llm_error is None
        assert "ReadTimeout" in result.embed_error

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

    def test_one_slow_row_does_not_switch_the_ai_off(self):
        """A healthy provider and one row whose answer outlasts llm_timeout_s:
        that row fails, every other row is still labelled."""
        from finance_copilot.pipeline.annotate import auto_annotate
        from finance_copilot.pipeline.llm import LLMTimeout

        self._unknown_rows(4)
        ok = AnnotationResponse(category="Shopping", confidence=0.7)
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=Exception("no embedder")), \
             patch(f"{_ANNOTATE}.annotate_transaction_llm",
                   side_effect=[LLMTimeout("no answer within the time limit"), ok, ok, ok]) as llm:
            result = auto_annotate(self.conn)

        assert llm.call_count == 4
        assert result.llm_annotated == 3
        assert result.llm_failed == 1
        assert result.llm_error is None

    def _rag_then_plain(self, rag_effect, plain_effect):
        """Run auto_annotate with every row going through rag_prompted (via the
        guard, as the real stage does) and then, on failure, the plain prompt."""
        from finance_copilot.pipeline import annotate

        rag_llm = MagicMock(side_effect=rag_effect)

        def fake_rag(conn, txn, category_list, ctx=None, *, as_of, guard=None, **_):
            guard.llm(rag_llm, txn, stage="rag_prompted")
            return None, None

        with patch.object(annotate, "_try_rag_annotation", side_effect=fake_rag), \
             patch.object(annotate, "annotate_transaction_llm", side_effect=plain_effect) as plain:
            return annotate.auto_annotate(self.conn), rag_llm, plain

    def test_one_slow_row_timing_out_in_both_stages_is_one_failed_row(self):
        """Review probe: a degenerate row times out at rag_prompted and again at
        the plain prompt. That's one bad row, not 'two in a row'."""
        from finance_copilot.pipeline.llm import LLMTimeout

        self._unknown_rows(4)
        slow = LLMTimeout("no answer within the time limit")
        ok = AnnotationResponse(category="Shopping", confidence=0.7)
        # u0 is slow everywhere; u1..u3 have no few-shot answer but a plain one.
        result, _, plain = self._rag_then_plain(
            rag_effect=[slow, None, None, None],
            plain_effect=[slow, ok, ok, ok],
        )

        assert plain.call_count == 4
        assert result.llm_annotated == 3
        assert result.llm_failed == 1
        assert result.llm_error is None

    def test_few_shot_prompt_rejected_still_labels_via_plain_prompt(self):
        """Review probe: the endpoint rejects the longer few-shot prompt (context
        window) but takes the plain one. rag_prompted goes off; every row is
        still labelled by the plain prompt, and no outage is reported."""
        self._unknown_rows(6)
        too_long = LLMRequestRejected("provider rejected this request (HTTP 400: context_length_exceeded)")
        ok = AnnotationResponse(category="Shopping", confidence=0.7)
        result, rag_llm, plain = self._rag_then_plain(rag_effect=too_long, plain_effect=lambda *_: ok)

        assert rag_llm.call_count == 5
        assert plain.call_count == 6
        assert result.llm_annotated == 6
        assert result.llm_error is None

    def test_hung_provider_trips_after_two_timed_out_rows(self):
        from finance_copilot.pipeline.annotate import auto_annotate
        from finance_copilot.pipeline.llm import LLMTimeout

        self._unknown_rows(6)
        with patch(f"{_ANNOTATE}.get_embedding_single", side_effect=Exception("no embedder")), \
             patch(f"{_ANNOTATE}.annotate_transaction_llm",
                   side_effect=LLMTimeout("no answer within the time limit")) as llm:
            result = auto_annotate(self.conn)

        assert llm.call_count == 2
        assert result.llm_failed == 6
        assert "timed out on 2 requests in a row" in result.llm_error


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
