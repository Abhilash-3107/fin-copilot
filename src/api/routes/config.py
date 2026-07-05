"""Client-facing config: runtime, UI-toggleable flags the UI reads and writes."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.deps import get_db
from src.config import settings
from src.db.queries.app_settings import get_dev_mode, set_dev_mode

router = APIRouter()


class ConfigPatch(BaseModel):
    dev_mode: bool


@router.get("")
def get_config(conn: sqlite3.Connection = Depends(get_db)):
    """Flags the frontend reads on startup. dev_mode gates the review-queue trace
    panel; confidence_threshold is the review bar the queue explains to the user."""
    return {
        "dev_mode": get_dev_mode(conn),
        "confidence_threshold": settings.confidence_threshold,
    }


@router.put("")
def update_config(body: ConfigPatch, conn: sqlite3.Connection = Depends(get_db)):
    """Persist a runtime flag toggled from the Settings page."""
    set_dev_mode(conn, body.dev_mode)
    conn.commit()
    return {"dev_mode": get_dev_mode(conn)}
