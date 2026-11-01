"""Data-management routes (4.14): full DB backup download and restore, and
delete-everything.

Export (CSV/JSON) lives on the transactions router; this module owns the
whole-DB operations the Settings › Data tab needs.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from finance_copilot.api.deps import get_db
from finance_copilot.config import settings
from finance_copilot.db.connection import get_connection

router = APIRouter()

# A multi-year personal ledger with vectors is tens of MB; this only stops a
# wrong file (a disk image, a video) from filling the disk.
MAX_RESTORE_BYTES = 1024 * 1024 * 1024
_SQLITE_HEADER = b"SQLite format 3\x00"
_LEDGER_TABLES = {"schema_migrations", "statements", "transactions", "annotations", "app_settings"}
_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

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


def _validate_backup(path: str) -> None:
    """422 unless `path` is an intact Finance Copilot ledger this build can open."""
    not_a_backup = HTTPException(
        status_code=422, detail="That file isn't a Finance Copilot backup."
    )
    with open(path, "rb") as f:
        if f.read(len(_SQLITE_HEADER)) != _SQLITE_HEADER:
            raise not_a_backup
    # Read-only and without the vec extension: validating must never write to
    # the upload, and integrity_check covers vec0's shadow tables as plain tables.
    probe = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if probe.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise HTTPException(status_code=422, detail="That backup is damaged and can't be restored.")
        tables = {r[0] for r in probe.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not _LEDGER_TABLES <= tables:
            raise not_a_backup
        applied = {r[0] for r in probe.execute("SELECT version FROM schema_migrations")}
    except sqlite3.DatabaseError as e:
        raise HTTPException(status_code=422, detail="That backup is damaged and can't be restored.") from e
    finally:
        probe.close()
    known = {p.name for p in _MIGRATIONS_DIR.glob("*.sql")}
    if applied - known:
        raise HTTPException(
            status_code=422,
            detail="This backup was made by a newer version of Finance Copilot. Update the app, then restore.",
        )


@router.post("/restore")
def restore_database(file: UploadFile, conn: sqlite3.Connection = Depends(get_db)):
    """Replace the ledger with an uploaded backup.

    The current ledger is first saved as pre-restore-<timestamp>.db next to the
    live one, so a wrong restore is recoverable. The swap uses SQLite's backup
    API into the live file rather than a file rename, which is safe with WAL
    and with other connections open. Backups carry no API key, so the current
    one is kept; everything else (model config, review threshold) comes from
    the backup.
    """
    from finance_copilot.db.lifecycle import reconcile_database

    data_dir = Path(settings.db_path).resolve().parent
    fd, upload_path = tempfile.mkstemp(suffix=".db", prefix="restore-", dir=data_dir)
    try:
        with os.fdopen(fd, "wb") as out:
            size = 0
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_RESTORE_BYTES:
                    raise HTTPException(status_code=413, detail="That file is too large to be a backup.")
                out.write(chunk)
        _validate_backup(upload_path)

        inflight = conn.execute(
            "SELECT COUNT(*) FROM annotation_jobs WHERE status IN ('queued','running')"
        ).fetchone()[0]
        if inflight:
            raise HTTPException(
                status_code=409,
                detail="A categorization or memory rebuild is running. Restore once it finishes.",
            )
        key_row = conn.execute("SELECT value FROM app_settings WHERE key = 'llm_api_key'").fetchone()

        safety_name = f"pre-restore-{datetime.now():%Y%m%d-%H%M%S}.db"
        live = get_connection(settings.db_path)
        try:
            safety = sqlite3.connect(data_dir / safety_name)
            try:
                live.backup(safety)
            finally:
                safety.close()
            upload = sqlite3.connect(upload_path)
            try:
                upload.backup(live)
            finally:
                upload.close()
            if key_row:
                live.execute(
                    "INSERT INTO app_settings (key, value) VALUES ('llm_api_key', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key_row[0],),
                )
                live.commit()
            reconcile_database(live, reset_llm_settings=True)
            counts = {
                table: live.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("statements", "transactions", "annotations")
            }
        finally:
            live.close()
    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.unlink(upload_path + suffix)
            except FileNotFoundError:
                pass
    return {**counts, "safety_copy": safety_name}


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
