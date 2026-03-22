"""Settings: database path, Ollama URL, confidence threshold, and related configuration."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_path: str = str(Path.home() / ".financebot" / "finance.db")
    ollama_url: str = "http://localhost:11434"
    confidence_threshold: float = 0.85
    api_base_url: str = "http://localhost:8000"

    # Last-segment values in UPI descriptions that carry no meaningful note.
    # Extend this list as you encounter new noise patterns — no code changes needed.
    upi_noise_keywords: list[str] = [
        "UPI", "NEFT", "IMPS", "RTGS", "NA", "NO REMARKS", "N/A", "NONE", "-",
    ]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
