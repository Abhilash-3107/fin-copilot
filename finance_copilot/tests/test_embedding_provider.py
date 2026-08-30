"""Embedding provider seam: dispatch, model identity, vec-dimension rebuild, reembed job."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from finance_copilot.config import current_embedding_dim, current_embedding_model, settings
from finance_copilot.db.connection import get_connection, init_db
from finance_copilot.db.queries.embeddings import ensure_vec_dimension
from finance_copilot.pipeline.embed import EmbeddingsUnavailable, get_embeddings_batch


class TestProviderDispatch:
    def test_none_raises_unavailable(self):
        with patch.object(settings, "embedding_provider", "none"):
            with pytest.raises(EmbeddingsUnavailable):
                get_embeddings_batch(["hello"])

    def test_local_uses_fastembed(self):
        import numpy as np

        fake = MagicMock()
        fake.embed.return_value = iter([np.array([0.1, 0.2])])
        with (
            patch.object(settings, "embedding_provider", "local"),
            patch("finance_copilot.pipeline.embed._get_local_embedder", return_value=fake),
        ):
            vecs = get_embeddings_batch(["hello"])
        assert vecs == [[pytest.approx(0.1), pytest.approx(0.2)]]
        fake.embed.assert_called_once_with(["hello"])

    def test_ollama_path_hits_http(self):
        response = MagicMock()
        response.json.return_value = {"embeddings": [[1.0, 2.0]]}
        with (
            patch.object(settings, "embedding_provider", "ollama"),
            patch("httpx.post", return_value=response) as post,
        ):
            vecs = get_embeddings_batch(["hello"])
        assert vecs == [[1.0, 2.0]]
        assert "/api/embed" in post.call_args.args[0]


class TestModelIdentity:
    def test_ollama_model(self):
        with patch.object(settings, "embedding_provider", "ollama"):
            assert current_embedding_model() == settings.ollama_embedding_model
            assert current_embedding_dim() == 768

    def test_local_model(self):
        with patch.object(settings, "embedding_provider", "local"):
            assert current_embedding_model() == settings.local_embedding_model
            assert current_embedding_dim() == 384


class TestEnsureVecDimension:
    @pytest.fixture
    def conn(self, tmp_path):
        c = get_connection(str(tmp_path / "t.db"))
        init_db(c)
        yield c
        c.close()

    def _vec_available(self, conn) -> bool:
        try:
            conn.execute("SELECT vec_version()")
            return True
        except sqlite3.OperationalError:
            return False

    def test_noop_when_dim_matches(self, conn):
        if not self._vec_available(conn):
            pytest.skip("sqlite-vec unavailable")
        with patch.object(settings, "embedding_provider", "ollama"):
            assert ensure_vec_dimension(conn) is False

    def test_rebuilds_on_dim_change(self, conn):
        if not self._vec_available(conn):
            pytest.skip("sqlite-vec unavailable")
        with patch.object(settings, "embedding_provider", "local"):
            assert ensure_vec_dimension(conn) is True
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='vec_items'"
            ).fetchone()[0]
            assert "384" in sql
            # embedding_meta cleared alongside the vectors
            assert conn.execute("SELECT COUNT(*) FROM embedding_meta").fetchone()[0] == 0
            # second call is a no-op
            assert ensure_vec_dimension(conn) is False

    def test_noop_without_vec_table(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        assert ensure_vec_dimension(c) is False


class TestReembedJob:
    @pytest.fixture
    def client_conn(self, tmp_path):
        from fastapi.testclient import TestClient

        from finance_copilot.api.deps import get_db as api_get_db
        from finance_copilot.main import app

        conn = get_connection(str(tmp_path / "t.db"))
        init_db(conn)
        app.dependency_overrides[api_get_db] = lambda: conn
        yield TestClient(app), conn
        app.dependency_overrides.pop(api_get_db, None)
        conn.close()

    def test_starts_and_completes(self, client_conn, tmp_path):
        client, conn = client_conn
        with (
            patch(
                "finance_copilot.api.routes.annotations._open_job_connection",
                side_effect=lambda: get_connection(str(tmp_path / "t.db")),
            ),
            patch(
                "finance_copilot.pipeline.embed.get_embeddings_batch",
                return_value=[],
            ),
        ):
            r = client.post("/api/embeddings/reembed")
            assert r.status_code == 202
            job_id = r.json()["job_id"]
            assert r.json()["kind"] == "reembed"
            # TestClient runs BackgroundTasks synchronously after the response.
            job = client.get(f"/api/annotations/jobs/{job_id}").json()
        assert job["status"] == "completed"
        assert job["kind"] == "reembed"

    def test_second_request_reattaches(self, client_conn):
        client, conn = client_conn
        conn.execute(
            "INSERT INTO annotation_jobs (id, kind, status) VALUES ('j1', 'reembed', 'running')"
        )
        conn.commit()
        r = client.post("/api/embeddings/reembed")
        assert r.status_code == 202
        assert r.json()["job_id"] == "j1"
