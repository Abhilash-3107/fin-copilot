"""Client-facing config: runtime, UI-toggleable flags the UI reads and writes."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from finance_copilot.api.deps import get_db
from finance_copilot.db.queries.app_settings import (
    get_confidence_threshold,
    get_dev_mode,
    get_llm_config,
    set_confidence_threshold,
    set_dev_mode,
    set_llm_config,
)

router = APIRouter()

_VALID_PROVIDERS = {"ollama", "openai", "none"}
_VALID_EMBEDDING_PROVIDERS = {"ollama", "local", "none"}


class ConfigPatch(BaseModel):
    dev_mode: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


@router.get("")
def get_config(conn: sqlite3.Connection = Depends(get_db)):
    """Flags the frontend reads on startup. dev_mode gates the review-queue trace
    panel; confidence_threshold is the review bar the queue explains and the
    Settings page tunes (a runtime override of the config default)."""
    return {
        "dev_mode": get_dev_mode(conn),
        "confidence_threshold": get_confidence_threshold(conn),
    }


@router.put("")
def update_config(body: ConfigPatch, conn: sqlite3.Connection = Depends(get_db)):
    """Persist runtime flags toggled from the Settings page. Only fields present
    in the request are changed."""
    if body.dev_mode is not None:
        set_dev_mode(conn, body.dev_mode)
    if body.confidence_threshold is not None:
        set_confidence_threshold(conn, body.confidence_threshold)
    conn.commit()
    return {
        "dev_mode": get_dev_mode(conn),
        "confidence_threshold": get_confidence_threshold(conn),
    }


class LlmConfigPatch(BaseModel):
    llm_provider: str | None = None
    ollama_url: str | None = None
    ollama_model: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    # Blank means "keep the stored key"; a real value replaces it. Never echoed back.
    llm_api_key: str | None = None
    embedding_provider: str | None = None
    local_embedding_model: str | None = None


@router.get("/llm")
def get_llm(conn: sqlite3.Connection = Depends(get_db)):
    """Effective BYOM/LLM config for the Settings › AI & Model form. The API key is
    never returned — only a boolean `llm_api_key_set` indicating one is stored."""
    return get_llm_config(conn)


@router.put("/llm")
def update_llm(body: LlmConfigPatch, conn: sqlite3.Connection = Depends(get_db)):
    """Persist BYOM/LLM config and apply it to the live pipeline."""
    values = body.model_dump(exclude_none=True)
    provider = values.get("llm_provider")
    if provider is not None and provider not in _VALID_PROVIDERS:
        raise HTTPException(status_code=422, detail=f"provider must be one of {sorted(_VALID_PROVIDERS)}")
    embed_provider = values.get("embedding_provider")
    if embed_provider is not None and embed_provider not in _VALID_EMBEDDING_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"embedding_provider must be one of {sorted(_VALID_EMBEDDING_PROVIDERS)}",
        )
    # Surface whether this save moved the vector space, so the UI can offer the
    # re-embed job (provider switch = full re-embed; single vector space).
    from finance_copilot.config import current_embedding_model

    model_before = current_embedding_model()
    set_llm_config(conn, values)
    conn.commit()
    out = get_llm_config(conn)
    out["embedding_model_changed"] = current_embedding_model() != model_before
    return out


@router.post("/llm/test")
def test_llm(conn: sqlite3.Connection = Depends(get_db)):
    """Probe the currently-saved provider so the user can validate before relying on
    it. Tests the persisted config (save first), reusing the live settings the PUT
    applied."""
    from finance_copilot.db.queries.app_settings import apply_llm_config
    from finance_copilot.pipeline.llm import test_connection

    apply_llm_config(conn)  # ensure the singleton reflects what's stored
    return test_connection()
