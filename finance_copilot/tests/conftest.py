"""Shared test isolation."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_llm_settings(monkeypatch):
    """PUT /api/config/llm applies the new model config onto the process-wide
    settings singleton. Snapshot those fields so a test that saves a config
    can't change which provider every later test runs against."""
    from finance_copilot.config import settings
    from finance_copilot.db.queries.app_settings import _LLM_KEYS

    for key in _LLM_KEYS:
        monkeypatch.setattr(settings, key, getattr(settings, key))
