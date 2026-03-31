"""SQLite connection setup and sqlite-vec extension loading."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection, load sqlite-vec, enable WAL and foreign keys."""
    from src.config import settings

    path = db_path or settings.db_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        pass  # sqlite-vec unavailable (e.g. unit test environments without the extension)

    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Apply all migrations in order, skipping vec_items if sqlite-vec is absent."""
    migrations_dir = Path(__file__).parent / "migrations"
    migration_files = sorted(migrations_dir.glob("*.sql"))

    for sql_path in migration_files:
        ddl = sql_path.read_text()
        lines = [ln for ln in ddl.splitlines() if not ln.strip().startswith("--")]
        cleaned = "\n".join(lines)

        for statement in cleaned.split(";"):
            stmt = statement.strip()
            if not stmt:
                continue
            if "vec_items" in stmt:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # sqlite-vec extension not loaded; skip virtual table
            else:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    err = str(e).lower()
                    # Ignore idempotent ALTER TABLE re-runs:
                    # - "duplicate column" when ADD COLUMN already applied
                    # - "no such column" when DROP COLUMN already applied
                    # - "no such table" when ALTER precedes CREATE in migration (fresh DB)
                    if "duplicate column" in err or "no such column" in err or (
                        stmt.upper().startswith("ALTER TABLE") and "no such table" in err
                    ):
                        pass
                    else:
                        raise

    conn.commit()


def get_db(db_path: str | None = None) -> sqlite3.Connection:
    """Return a fully initialised, migration-applied connection."""
    conn = get_connection(db_path)
    init_db(conn)
    return conn
