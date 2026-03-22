"""FastAPI app entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from financebot.db.connection import get_db
from financebot.api.routes import annotations, groups, people, statements, transactions

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


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui/index.html")


app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")
