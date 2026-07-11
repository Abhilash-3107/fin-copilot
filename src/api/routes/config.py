"""Client-facing config: runtime, UI-toggleable flags the UI reads and writes."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.deps import get_db
from src.db.queries.app_settings import (
    get_confidence_threshold,
    get_dev_mode,
    get_llm_config,
    set_confidence_threshold,
    set_dev_mode,
    set_llm_config,
)

router = APIRouter()

_VALID_PROVIDERS = {"ollama", "openai", "none"}


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
    set_llm_config(conn, values)
    conn.commit()
    return get_llm_config(conn)


@router.post("/llm/test")
def test_llm(conn: sqlite3.Connection = Depends(get_db)):
    """Probe the currently-saved provider so the user can validate before relying on
    it. Tests the persisted config (save first), reusing the live settings the PUT
    applied."""
    from src.db.queries.app_settings import apply_llm_config
    from src.pipeline.llm import test_connection

    apply_llm_config(conn)  # ensure the singleton reflects what's stored
    return test_connection()
