"""FastAPI app entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
# Per-module overrides: set pipeline to DEBUG for full per-transaction detail
logging.getLogger("src.pipeline").setLevel(logging.DEBUG)
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.db.connection import get_db
from src.api.routes import annotations, embeddings, groups, people, statements, transactions

UI_DIR = Path(__file__).parent.parent / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = get_db()
    conn.close()
    yield


app = FastAPI(title="Finance Copilot", lifespan=lifespan)

app.include_router(statements.router, prefix="/statements", tags=["statements"])
app.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
app.include_router(annotations.router, prefix="/annotations", tags=["annotations"])
app.include_router(groups.router, prefix="/groups", tags=["groups"])
app.include_router(people.router, prefix="/people", tags=["people"])
app.include_router(embeddings.router, prefix="/embeddings", tags=["embeddings"])


@app.get("/health")
def health():
    """Check Ollama availability and which models are loaded vs pulled."""
    ollama_ok = False
    loaded: list[str] = []
    pulled: list[str] = []

    try:
        ps = httpx.get(f"{settings.ollama_url}/api/ps", timeout=3.0)
        ps.raise_for_status()
        loaded = [m["name"] for m in ps.json().get("models", [])]
        ollama_ok = True
    except Exception:
        pass

    if ollama_ok:
        try:
            tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3.0)
            tags.raise_for_status()
            pulled = [m["name"] for m in tags.json().get("models", [])]
        except Exception:
            pass

    def _model_status(name: str) -> dict:
        # Ollama names may include tags like "qwen3.5:7b" — match prefix too
        is_loaded = any(m == name or m.startswith(name + ":") for m in loaded)
        is_pulled = any(m == name or m.startswith(name + ":") for m in pulled)
        if is_loaded:
            status = "running"
        elif is_pulled:
            status = "available"
        else:
            status = "not_pulled"
        return {"name": name, "status": status}

    return {
        "ollama": "ok" if ollama_ok else "unavailable",
        "chat_model": _model_status(settings.ollama_model),
        "embedding_model": _model_status(settings.ollama_embedding_model),
    }


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui/index.html")


app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")
