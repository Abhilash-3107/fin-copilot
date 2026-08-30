"""Read/write helpers for runtime app settings (the app_settings key-value table).

These are settings the UI can toggle at runtime and the backend reads live — unlike
finance_copilot.config.Settings, which is process-static env/.env config. The env value (e.g.
DEV_MODE) only seeds the initial default the first time a key is read.
"""
from __future__ import annotations

import sqlite3

from finance_copilot.config import settings

_DEV_MODE_KEY = "dev_mode"
_CONFIDENCE_THRESHOLD_KEY = "confidence_threshold"

# BYOM: LLM provider config the user edits at runtime (Settings › AI & Model),
# stored here and applied onto the process-static `settings` so the pipeline reads
# it live. Each maps to a field on finance_copilot.config.Settings. api_key is stored but never
# returned to the client (see get_llm_config).
_LLM_KEYS = (
    "llm_provider",
    "ollama_url",
    "ollama_model",
    "llm_base_url",
    "llm_model",
    "llm_api_key",
)


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value),
    )


def get_dev_mode(conn: sqlite3.Connection) -> bool:
    """Current dev_mode, falling back to the DEV_MODE env default when unset."""
    stored = _get(conn, _DEV_MODE_KEY)
    if stored is None:
        return settings.dev_mode
    return stored == "true"


def set_dev_mode(conn: sqlite3.Connection, enabled: bool) -> None:
    """Persist dev_mode. Caller commits."""
    _set(conn, _DEV_MODE_KEY, "true" if enabled else "false")


def get_confidence_threshold(conn: sqlite3.Connection) -> float:
    """Current review-bar threshold, falling back to the config default when unset.

    This is the read-time gate the review queue and the dev-mode summary apply, so
    the user can tune "what needs review" from the UI without re-running the
    pipeline (stored per-annotation confidence is unchanged; only the cutoff moves).
    """
    stored = _get(conn, _CONFIDENCE_THRESHOLD_KEY)
    if stored is None:
        return settings.confidence_threshold
    try:
        return float(stored)
    except ValueError:
        return settings.confidence_threshold


def set_confidence_threshold(conn: sqlite3.Connection, value: float) -> None:
    """Persist the review-bar threshold, clamped to [0, 1]. Caller commits."""
    clamped = max(0.0, min(1.0, float(value)))
    _set(conn, _CONFIDENCE_THRESHOLD_KEY, repr(clamped))


def get_llm_config(conn: sqlite3.Connection, include_secret: bool = False) -> dict:
    """Effective LLM config: stored runtime overrides falling back to the env/config
    defaults. api_key is redacted to a boolean flag unless include_secret is set (it
    must never be returned to the client)."""
    cfg = {key: (_get(conn, key) if _get(conn, key) is not None else getattr(settings, key)) for key in _LLM_KEYS}
    if not include_secret:
        cfg["llm_api_key_set"] = bool(cfg.pop("llm_api_key", ""))
    return cfg


def set_llm_config(conn: sqlite3.Connection, values: dict) -> None:
    """Persist LLM config overrides and apply them onto the live settings singleton.

    Only keys present in `values` are changed. An empty api_key value is ignored
    (the UI sends it blank to mean 'leave the stored key untouched'); pass the
    sentinel '' via a dedicated clear action if you need to wipe it."""
    for key in _LLM_KEYS:
        if key not in values or values[key] is None:
            continue
        val = values[key]
        if key == "llm_api_key" and val == "":
            continue  # blank = keep existing key
        _set(conn, key, val)
    apply_llm_config(conn)


def ensure_llm_provider_default(conn: sqlite3.Connection, probe_timeout: float = 1.5) -> None:
    """First-run provider choice: detect Ollama once, then never probe again.

    A fresh install has no stored llm_provider. If the config default is still
    "ollama" (i.e. the user didn't explicitly choose a provider via env), probe
    the default Ollama URL one time: reachable keeps today's behavior, absent
    stores "none" so the app presents rules + history as the normal tier instead
    of timing out against a server that isn't there. The stored value makes the
    choice stable; Settings > AI & Model changes it any time. Caller commits.
    """
    if _get(conn, "llm_provider") is not None:
        return  # already decided (by this probe or by the user)
    if settings.llm_provider != "ollama":
        return  # explicit env choice ("openai"/"none"); respect it, store nothing
    import httpx

    try:
        httpx.get(f"{settings.ollama_url}/api/tags", timeout=probe_timeout).raise_for_status()
        detected = "ollama"
    except Exception:
        detected = "none"
    _set(conn, "llm_provider", detected)


def apply_llm_config(conn: sqlite3.Connection) -> None:
    """Copy stored LLM overrides onto the process-static `settings` so the pipeline
    (which reads the singleton) uses the user's runtime choices. Called on write and
    at app startup."""
    for key in _LLM_KEYS:
        stored = _get(conn, key)
        if stored is not None:
            setattr(settings, key, stored)
