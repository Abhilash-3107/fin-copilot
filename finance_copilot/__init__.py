"""Personal finance budgeting automation - local-first ingestion, embeddings, and RAG annotation."""
from __future__ import annotations

from importlib import metadata


def app_version() -> str:
    """The installed package version; pyproject.toml is the single source."""
    try:
        return metadata.version("finance-copilot")
    except metadata.PackageNotFoundError:
        return "0.0.0.dev0 (checkout)"
