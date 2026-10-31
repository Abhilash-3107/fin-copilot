"""FastAPI app entry point."""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from finance_copilot.config import settings

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
from fastapi.staticfiles import StaticFiles

from finance_copilot.api.routes import (
    annotations,
    categories,
    config,
    data,
    embeddings,
    groups,
    insights,
    local_llm,
    people,
    statements,
    transactions,
)
from finance_copilot.db.connection import get_migrated_db

# The built SPA rides inside the wheel as finance_copilot/ui_dist (force-included
# at build time); a dev checkout serves ui/dist instead. Wheels install as plain
# directories, so Path(__file__) resolution covers both cases.
_BUNDLED_DIST = Path(__file__).parent / "ui_dist"
_CHECKOUT_DIST = Path(__file__).parent.parent / "ui" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Apply pending migrations once at startup; per-request connections skip this.
    conn = get_migrated_db()
    try:
        # Apply any stored BYOM/LLM overrides onto the live settings so the pipeline
        # uses the user's runtime provider choice from the first request.
        from finance_copilot.db.queries.app_settings import (
            apply_llm_config,
            ensure_llm_provider_default,
        )

        # First run only: pick "ollama" vs "none" from a one-shot probe so a
        # machine without Ollama starts in the honest no-AI tier instead of
        # timing out per request.
        ensure_llm_provider_default(conn)
        conn.commit()
        apply_llm_config(conn)
        # Vector table must match the active embedding model's dimension; a
        # provider switch while the app was down is reconciled here.
        from finance_copilot.db.queries.embeddings import ensure_vec_dimension

        if ensure_vec_dimension(conn):
            logging.getLogger(__name__).warning(
                "embedding dimension changed - vector memory cleared; run a re-embed "
                "from Settings > AI & Model to rebuild RAG retrieval"
            )
        # Jobs still marked in-flight at boot were orphaned by the previous
        # process; fail them or they block the single-job guard forever.
        reaped = annotations.reap_interrupted_jobs(conn)
        if reaped:
            logging.getLogger(__name__).warning(
                "failed %d annotation job(s) orphaned by the previous process", reaped
            )
    finally:
        conn.close()
    yield


app = FastAPI(title="Finance Copilot", lifespan=lifespan)


# Opt-in token gate (4.9): when settings.api_token is set, every /api request
# except health must present it. Off by default so the loopback single-user case
# is unaffected; this exists for a deliberate LAN bind where the app would
# otherwise serve bank statements to the whole network with no auth.
# FastAPI's schema and docs pages sit outside /api/; they describe every route,
# so they're gated along with it.
_SCHEMA_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})


def _extract_token(request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-token")


# Demo-instance guardrails (FC_DEMO_MODE=1): the hosted demo must be incapable
# of receiving real user data or leaking anything, at the API layer - hiding
# buttons in the UI is not a security boundary. (method, path-prefix) pairs.
_DEMO_BLOCKED = (
    ("POST", "/api/statements/upload"),   # real data must never land here
    ("DELETE", "/api/statements"),        # keep the shared dataset intact
    ("GET", "/api/data/backup"),          # nothing to exfiltrate, keep it that way
    ("POST", "/api/data/delete-all"),
    ("PUT", "/api/config/llm"),           # no provider/API-key writes on a shared box
    ("POST", "/api/config/llm/test"),
    ("POST", "/api/local-llm"),           # no multi-GB downloads onto the demo box
    ("DELETE", "/api/local-llm"),
)


@app.middleware("http")
async def demo_mode_guard(request, call_next):
    from fastapi.responses import JSONResponse

    if settings.demo_mode:
        path = request.url.path
        for method, prefix in _DEMO_BLOCKED:
            if request.method == method and path.startswith(prefix):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "This is the shared demo - it never accepts real data. "
                        "Run Finance Copilot locally (uvx finance-copilot) to use your own statements."
                    },
                )
    return await call_next(request)


@app.middleware("http")
async def require_api_token(request, call_next):
    from fastapi.responses import JSONResponse

    token = settings.api_token
    path = request.url.path
    gated = (path.startswith("/api/") and path != "/api/health") or path in _SCHEMA_PATHS
    if token and gated:
        presented = (_extract_token(request) or "").encode()
        # Constant-time so a LAN client can't recover the secret byte by byte.
        if not secrets.compare_digest(presented, token.encode()):
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid API token"})
    return await call_next(request)


# All API routes live under /api so they can never collide with SPA client routes.
app.include_router(statements.router, prefix="/api/statements", tags=["statements"])
app.include_router(transactions.router, prefix="/api/transactions", tags=["transactions"])
app.include_router(annotations.router, prefix="/api/annotations", tags=["annotations"])
app.include_router(groups.router, prefix="/api/groups", tags=["groups"])
app.include_router(people.router, prefix="/api/people", tags=["people"])
app.include_router(embeddings.router, prefix="/api/embeddings", tags=["embeddings"])
app.include_router(categories.router, prefix="/api/categories", tags=["categories"])
app.include_router(config.router, prefix="/api/config", tags=["config"])
app.include_router(insights.router, prefix="/api/insights", tags=["insights"])
app.include_router(data.router, prefix="/api/data", tags=["data"])
app.include_router(local_llm.router, prefix="/api/local-llm", tags=["local-llm"])


def _app_version() -> str:
    from importlib import metadata

    try:
        return metadata.version("finance-copilot")
    except metadata.PackageNotFoundError:
        return "0.0.0.dev0"


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

    # One plain sentence for the UI when a capability is off - the no-AI state
    # is a tier, not a failure.
    provider = settings.llm_provider
    if provider == "none":
        ai_message = (
            "AI is off. Parsing, rules, and your saved history still categorize "
            "what they can; add a provider in Settings > AI & Model for the rest."
        )
    elif provider == "ollama" and not ollama_ok:
        ai_message = "Ollama isn't reachable right now, so AI categorization is paused."
    else:
        ai_message = ""
    if settings.embedding_provider == "local":
        embeddings_status = "ok"  # in-process; weights download on first embed
    elif settings.embedding_provider == "none":
        embeddings_status = "off"
    else:
        embeddings_status = "ok" if ollama_ok else "unavailable"

    return {
        "version": _app_version(),
        "llm_provider": provider,
        "ai_message": ai_message,
        "embedding_provider": settings.embedding_provider,
        "embeddings": embeddings_status,
        "ollama": "ok" if ollama_ok else "unavailable",
        "chat_model": _model_status(settings.ollama_model),
        "embedding_model": _model_status(settings.ollama_embedding_model),
        # Lets the SPA know it must present a token (prompt for it) without
        # leaking the token itself.
        "auth_required": bool(settings.api_token),
        # The SPA shows the persistent "sample data" banner off this flag.
        "demo_mode": settings.demo_mode,
    }


DIST_DIR = _BUNDLED_DIST if _BUNDLED_DIST.exists() else _CHECKOUT_DIST

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
