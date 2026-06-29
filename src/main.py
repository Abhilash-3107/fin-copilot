"""FastAPI app entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from src.config import settings

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
from fastapi.staticfiles import StaticFiles

from src.db.connection import get_db
from src.api.routes import annotations, categories, config, embeddings, groups, people, statements, transactions

UI_DIR = Path(__file__).parent.parent / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Apply pending migrations once at startup; per-request connections skip this.
    conn = get_db()
    conn.close()
    yield


app = FastAPI(title="Finance Copilot", lifespan=lifespan)

# All API routes live under /api so they can never collide with SPA client routes.
app.include_router(statements.router, prefix="/api/statements", tags=["statements"])
app.include_router(transactions.router, prefix="/api/transactions", tags=["transactions"])
app.include_router(annotations.router, prefix="/api/annotations", tags=["annotations"])
app.include_router(groups.router, prefix="/api/groups", tags=["groups"])
app.include_router(people.router, prefix="/api/people", tags=["people"])
app.include_router(embeddings.router, prefix="/api/embeddings", tags=["embeddings"])
app.include_router(categories.router, prefix="/api/categories", tags=["categories"])
app.include_router(config.router, prefix="/api/config", tags=["config"])


@app.get("/api/health")
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


DIST_DIR = UI_DIR / "dist"

if DIST_DIR.exists():
    from fastapi.exceptions import HTTPException
    from fastapi.responses import FileResponse

    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    # Catch-all SPA fallback (registered last): client routes like /review or
    # /insights resolve to index.html on refresh/deep link; unknown /api paths
    # still 404 as JSON.
    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (DIST_DIR / full_path).resolve()
        if full_path and candidate.is_relative_to(DIST_DIR.resolve()) and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(DIST_DIR / "index.html"))

else:
    @app.get("/", include_in_schema=False)
    def root():
        return {
            "message": "UI not built. Run `npm run dev` in ui/ for development, "
                       "or `npm run build` to serve the production bundle from here.",
            "api_docs": "/docs",
        }
