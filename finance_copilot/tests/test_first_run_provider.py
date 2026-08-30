"""First-run provider detection: one-shot Ollama probe seeds llm_provider."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from finance_copilot.config import settings
from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.app_settings import ensure_llm_provider_default


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _stored_provider(conn):
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'llm_provider'"
    ).fetchone()
    return row[0] if row else None


class TestEnsureLlmProviderDefault:
    def test_ollama_reachable_keeps_ollama(self, conn):
        with patch("httpx.get", return_value=MagicMock(raise_for_status=lambda: None)):
            ensure_llm_provider_default(conn)
        assert _stored_provider(conn) == "ollama"

    def test_ollama_absent_stores_none(self, conn):
        with patch("httpx.get", side_effect=ConnectionError):
            ensure_llm_provider_default(conn)
        assert _stored_provider(conn) == "none"

    def test_existing_choice_never_reprobed(self, conn):
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('llm_provider', 'openai')"
        )
        with patch("httpx.get") as get:
            ensure_llm_provider_default(conn)
        get.assert_not_called()
        assert _stored_provider(conn) == "openai"

    def test_explicit_env_provider_respected(self, conn):
        with (
            patch.object(settings, "llm_provider", "openai"),
            patch("httpx.get") as get,
        ):
            ensure_llm_provider_default(conn)
        get.assert_not_called()
        assert _stored_provider(conn) is None
