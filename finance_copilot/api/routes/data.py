"""Data-management routes (4.14): full DB backup download and delete-everything.

Export (CSV/JSON) lives on the transactions router; this module owns the two
destructive/whole-DB operations the Settings › Data tab needs.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from finance_copilot.api.deps import get_db
from finance_copilot.config import settings

router = APIRouter()

# User data cleared by delete-everything, in FK-safe order (children first).
# categories is the seeded taxonomy and is deliberately preserved. vec_items is
# handled separately because it's a virtual table that may be absent.
_WIPE_ORDER = (
    "transaction_links",
    "transaction_group_members",
    "transaction_groups",
    "annotation_jobs",
    "embedding_meta",
    "annotations",
    "feedback_stats",
    "learned_rule_suppressions",
    "people",
    "transactions",
    "statements",
    "app_settings",
)


@router.get("/backup")
def backup_database():
    """Download a consistent snapshot of the whole SQLite database.

    Uses SQLite's online backup API so the copy is transactionally consistent
    even with WAL writes in flight - a plain file copy could catch a torn write.
    The snapshot streams from a temp file that's unlinked once sent.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    src = sqlite3.connect(settings.db_path)
    dst = sqlite3.connect(tmp_path)
    try:
        with dst:
            src.backup(dst)
        # Backups get shared (bug reports, a new machine); the BYOM API key must
        # not travel with them. VACUUM so the deleted value doesn't linger on a
        # free page. Restore keeps whatever key the running install has.
        dst.execute("DELETE FROM app_settings WHERE key = 'llm_api_key'")
        dst.commit()
        dst.execute("VACUUM")
    finally:
        src.close()
        dst.close()

    return FileResponse(
        tmp_path,
        media_type="application/x-sqlite3",
        filename="finance-copilot-backup.db",
        background=BackgroundTask(os.unlink, tmp_path),
    )


@router.post("/demo")
def load_demo_data(conn: sqlite3.Connection = Depends(get_db)):
    """Seed the bundled demo ledger so a fresh install has something to explore.

    Refused once real statements exist - demo rows must never mix into a real
    ledger. Re-running on an already-demo DB is a no-op top-up (idempotent
    inserts); delete-everything is the way back.
    """
    from datetime import date

    from fastapi import HTTPException

    from finance_copilot.demo_seed import seed

    non_demo = conn.execute(
        "SELECT COUNT(*) FROM statements WHERE id NOT LIKE 'demo_stmt%'"
    ).fetchone()[0]
    if non_demo:
        raise HTTPException(
            status_code=409,
            detail="You already have real statements - demo data won't be mixed into them.",
        )
    return seed(conn, date.today())


@router.post("/delete-all")
def delete_all(conn: sqlite3.Connection = Depends(get_db)):
    """Irreversibly remove all user data (statements, transactions, annotations,
    groups, people, embeddings, settings), keeping only the seeded category
    taxonomy. Vacuum afterward so the file actually shrinks."""
    deleted: dict[str, int] = {}
    # Defer FK enforcement so the wipe order can't trip on a transient dangling
    # reference mid-batch; the set we clear is self-contained.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        for table in _WIPE_ORDER:
            cur = conn.execute(f"DELETE FROM {table}")
            deleted[table] = cur.rowcount
        # Virtual vector table may not exist without the sqlite-vec extension.
        try:
            conn.execute("DELETE FROM vec_items")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("VACUUM")
    return {"deleted": deleted}
