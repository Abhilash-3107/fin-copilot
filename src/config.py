"""Settings: database path, Ollama URL, confidence threshold, and related configuration."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_path: str = str(Path(__file__).parent.parent / "data" / "finance.db")
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:4b"
    confidence_threshold: float = 0.85
    ollama_embedding_model: str = "nomic-embed-text"
    rag_direct_threshold: float = 0.92
    rag_top_k: int = 5
    rag_similarity_floor: float = 0.65
    rag_agreement_exponent: float = 0.3
    rag_margin_safe: float = 0.08
    llm_confidence_dampen: float = 0.85
    llm_confidence_dampen_rag: float = 0.92
    api_base_url: str = "http://localhost:8000"

    # Last-segment values in UPI descriptions that carry no meaningful note.
    # Extend this list as you encounter new noise patterns — no code changes needed.
    upi_noise_keywords: list[str] = [
        "UPI", "NEFT", "IMPS", "RTGS", "NA", "NO REMARKS", "N/A", "NONE", "-",
    ]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
