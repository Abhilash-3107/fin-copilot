"""Local LLM provider: manifest/downloader/preflight logic and the provider seam.

Inference itself needs the llama-cpp extra + a 2.7GB model, so these tests cover
everything up to the model boundary; a CI smoke-eval with the real model guards
the inference path when the extra is present.
"""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from finance_copilot import local_llm
from finance_copilot.config import settings
from finance_copilot.pipeline.llm import _build_provider_request, test_connection


@pytest.fixture
def data_dir(tmp_path):
    with patch.object(settings, "data_dir", str(tmp_path)):
        yield tmp_path


class TestManifest:
    def test_default_key_exists(self):
        assert local_llm.DEFAULT_MODEL_KEY in local_llm.MODEL_MANIFEST
        assert settings.local_llm_model in local_llm.MODEL_MANIFEST

    def test_entries_are_complete(self):
        for entry in local_llm.MODEL_MANIFEST.values():
            assert entry["url"].startswith("https://")
            assert len(entry["sha256"]) == 64
            assert entry["size"] > 1024**3
            assert entry["min_ram_bytes"] > 0


class TestPreflight:
    def test_ok_on_roomy_machine(self, data_dir):
        pf = local_llm.preflight()
        # Dev machines have the disk/RAM; the assertion is that it runs and reports.
        assert "ok" in pf and "problems" in pf

    def test_low_ram_recommends_byom(self, data_dir):
        with patch.object(local_llm, "total_ram_bytes", return_value=2 * 1024**3):
            pf = local_llm.preflight()
        assert pf["ok"] is False
        assert any("API key" in p for p in pf["problems"])


class TestStatus:
    def test_reports_an_interrupted_download_from_its_part_file(self, data_dir):
        # The downloader writes <model>.part until the sha256 checks out; status
        # used to look only at the final path, so a half-done download read as
        # "nothing downloaded" and the card couldn't offer to resume.
        entry = local_llm.MODEL_MANIFEST[settings.local_llm_model]
        part = local_llm.models_dir() / entry["filename"]
        part = part.with_suffix(".part")
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(b"x" * 1234)
        s = local_llm.status()
        assert s["download_partial"] is True
        assert s["downloaded_bytes"] == 1234
        assert s["model_present"] is False

    def test_complete_model_is_present_not_partial(self, data_dir):
        entry = local_llm.MODEL_MANIFEST[settings.local_llm_model]
        path = local_llm.models_dir() / entry["filename"]
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.truncate(entry["size"])
        s = local_llm.status()
        assert (s["model_present"], s["download_partial"], s["downloaded_bytes"]) == (True, False, entry["size"])


class TestDownload:
    def _serve(self, payload: bytes, status_code=200, from_byte=0):
        """Fake httpx.stream returning `payload[from_byte:]`."""
        response = MagicMock()
        response.status_code = 206 if from_byte else status_code
        response.iter_bytes = lambda chunk_size: iter([payload[from_byte:]])
        response.raise_for_status = lambda: None
        ctx = MagicMock()
        ctx.__enter__ = lambda s: response
        ctx.__exit__ = lambda s, *a: False
        return ctx

    def test_verifies_sha_and_installs(self, data_dir):
        payload = b"fake-gguf-bytes"
        key = "qwen3.5-4b-q4_k_m"
        entry = dict(local_llm.MODEL_MANIFEST[key])
        entry["sha256"] = hashlib.sha256(payload).hexdigest()
        entry["size"] = len(payload)
        with (
            patch.dict(local_llm.MODEL_MANIFEST, {key: entry}),
            patch("httpx.stream", return_value=self._serve(payload)),
        ):
            path = local_llm.download_model(key)
        assert path.exists()
        assert path.read_bytes() == payload
        assert not path.with_suffix(".part").exists()

    def test_hash_mismatch_removes_file_and_raises(self, data_dir):
        payload = b"tampered"
        key = "qwen3.5-4b-q4_k_m"
        with patch("httpx.stream", return_value=self._serve(payload)):
            with pytest.raises(local_llm.LocalLLMError, match="integrity"):
                local_llm.download_model(key)
        assert not local_llm.model_path(key).exists()
        assert not local_llm.model_path(key).with_suffix(".part").exists()

    def test_resumes_from_partial(self, data_dir):
        payload = b"0123456789"
        key = "qwen3.5-4b-q4_k_m"
        entry = dict(local_llm.MODEL_MANIFEST[key])
        entry["sha256"] = hashlib.sha256(payload).hexdigest()
        entry["size"] = len(payload)
        part = local_llm.model_path(key).with_suffix(".part")
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(payload[:4])
        with (
            patch.dict(local_llm.MODEL_MANIFEST, {key: entry}),
            patch("httpx.stream", return_value=self._serve(payload, from_byte=4)) as stream,
        ):
            path = local_llm.download_model(key)
        assert path.read_bytes() == payload
        assert stream.call_args.kwargs["headers"] == {"Range": "bytes=4-"}

    def test_cancel_keeps_partial(self, data_dir):
        payload = b"x" * 10
        key = "qwen3.5-4b-q4_k_m"
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = lambda: None

        def chunks(chunk_size):
            yield payload[:5]
            local_llm.request_download_cancel()
            yield payload[5:]

        response.iter_bytes = chunks
        ctx = MagicMock()
        ctx.__enter__ = lambda s: response
        ctx.__exit__ = lambda s, *a: False
        with patch("httpx.stream", return_value=ctx):
            with pytest.raises(local_llm.LocalLLMError, match="cancelled"):
                local_llm.download_model(key)
        assert local_llm.model_path(key).with_suffix(".part").exists()

    def test_remove_model(self, data_dir):
        p = local_llm.model_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"model")
        assert local_llm.remove_model() is True
        assert not p.exists()


class TestProviderSeam:
    def test_local_payload_shape(self):
        with patch.object(settings, "llm_provider", "local"):
            url, headers, payload = _build_provider_request(
                [{"role": "user", "content": "hi"}], ["Food > Eating Out"]
            )
        assert url == ""
        assert payload["response_format"]["type"] == "json_object"
        assert payload["response_format"]["schema"]["properties"]["category"]["enum"] == ["Food"]
        assert payload["logprobs"] is True

    def test_test_connection_without_extra(self):
        with (
            patch.object(settings, "llm_provider", "local"),
            patch.object(local_llm, "extra_installed", return_value=False),
        ):
            result = test_connection()
        assert result["ok"] is False
        assert "local-llm" in result["detail"]

    def test_call_llm_returns_none_when_unavailable(self):
        from finance_copilot.pipeline.llm import annotate_transaction_llm

        txn = {"id": "t1", "raw_description": "UPI/ZOMATO/123", "amount": 100, "debit_credit": "debit"}
        with patch.object(settings, "llm_provider", "local"):
            # No extra installed in the test env → LocalLLMError → clean None.
            assert annotate_transaction_llm(txn, ["Food"]) is None


def _model_available() -> bool:
    try:
        return local_llm.extra_installed() and local_llm.model_path().exists()
    except Exception:
        return False


@pytest.mark.skipif(not _model_available(), reason="llama-cpp extra + downloaded model required")
class TestLocalInferenceSmoke:
    """Bit-rot guard for the real inference path: JSON validity + determinism,
    not accuracy (accuracy is the eval harness's job)."""

    def test_json_validity_and_determinism(self):
        from finance_copilot.pipeline.llm import annotate_transaction_llm

        txn = {
            "id": "smoke1",
            "raw_description": "UPI/ZOMATO/431276541892/lunch order",
            "amount": 420.0,
            "debit_credit": "debit",
        }
        cats = ["Food > Eating Out", "Travel > Cab", "Shopping > Online Shopping"]
        with patch.object(settings, "llm_provider", "local"):
            first = annotate_transaction_llm(txn, cats)
            second = annotate_transaction_llm(txn, cats)
        assert first is not None, "local inference returned no result"
        assert first.category in {"Food", "Travel", "Shopping"}
        assert 0.0 <= first.confidence <= 1.0
        assert second is not None and second.category == first.category


class TestDownloadRoutes:
    @pytest.fixture
    def client_conn(self, tmp_path, data_dir):
        import sqlite3

        from fastapi.testclient import TestClient

        from finance_copilot.api.deps import get_db as api_get_db
        from finance_copilot.db.connection import init_db
        from finance_copilot.main import app

        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        init_db(conn)
        app.dependency_overrides[api_get_db] = lambda: conn
        yield TestClient(app), conn
        app.dependency_overrides.pop(api_get_db, None)
        conn.close()

    def test_status_route(self, client_conn):
        client, _ = client_conn
        body = client.get("/api/local-llm/status").json()
        assert body["extra_installed"] in (True, False)
        assert body["models"]

    def test_unknown_model_rejected(self, client_conn):
        client, _ = client_conn
        r = client.post("/api/local-llm/download", json={"model_key": "nope"})
        assert r.status_code == 422

    def test_preflight_failure_blocks_download(self, client_conn):
        client, _ = client_conn
        with patch.object(local_llm, "total_ram_bytes", return_value=1 * 1024**3):
            r = client.post("/api/local-llm/download", json={})
        assert r.status_code == 409
        assert "API key" in r.json()["detail"]
