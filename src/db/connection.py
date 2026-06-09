"""SQLite connection setup, sqlite-vec extension loading, and the migration runner."""
from __future__ import annotations

import sqlite3
from pathlib import Path

# Migrations that predate the schema_migrations table. Databases created by the
# old runner already have all of these applied, so when we encounter a DB with
# tables but no schema_migrations rows, we baseline these as applied.
_PRE_TRACKING_MIGRATIONS = frozenset({
    "001_initial.sql",
    "002_links.sql",
    "003_people.sql",
    "004_member_type.sql",
    "005_drop_group_type.sql",
    "006_seed_categories.sql",
    "007_expand_annotation_source.sql",
    "008_feedback_stats.sql",
    "009_add_reimbursement_category.sql",
})


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection, load sqlite-vec, enable WAL and foreign keys."""
    from src.config import settings

    path = db_path or settings.db_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        pass  # sqlite-vec unavailable (e.g. unit test environments without the extension)

    return conn


def _strip_comments(stmt: str) -> str:
    return "\n".join(
        ln for ln in stmt.splitlines() if not ln.strip().startswith("--")
    ).strip()


def _iter_statements(sql: str):
    """Split a migration file into complete SQL statements, comment lines removed.

    Uses sqlite3.complete_statement so semicolons inside string literals or
    trigger bodies don't break statements apart.
    """
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            stmt = _strip_comments(buffer)
            if stmt:
                yield stmt
            buffer = ""
    tail = _strip_comments(buffer)  # trailing statement without a semicolon
    if tail:
        yield tail


def _has_vec(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT vec_version()")
        return True
    except sqlite3.OperationalError:
        return False


def init_db(conn: sqlite3.Connection) -> None:
    """Apply pending migrations exactly once each, tracked in schema_migrations.

    Each migration file runs inside a transaction: it either applies fully and is
    recorded, or rolls back and raises. Statements touching the vec_items virtual
    table are skipped when the sqlite-vec extension is unavailable.
    """
    migrations_dir = Path(__file__).parent / "migrations"
    migration_files = sorted(migrations_dir.glob("*.sql"))

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT (datetime('now'))
        )
        """
    )

    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    # Baseline databases created before migration tracking existed: the old
    # runner re-applied everything on each connection, so existing tables imply
    # the pre-tracking migrations are all in place.
    if not applied:
        has_tables = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='statements'"
        ).fetchone()
        if has_tables:
            conn.executemany(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                [(name,) for name in sorted(_PRE_TRACKING_MIGRATIONS)],
            )
            applied = set(_PRE_TRACKING_MIGRATIONS)
    conn.commit()

    vec_available = _has_vec(conn)

    for sql_path in migration_files:
        if sql_path.name in applied:
            continue
        try:
            for stmt in _iter_statements(sql_path.read_text()):
                if "vec_items" in stmt and not vec_available:
                    continue
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)", (sql_path.name,)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def get_db(db_path: str | None = None) -> sqlite3.Connection:
    """Return a fully initialised, migration-applied connection."""
    conn = get_connection(db_path)
    init_db(conn)
    return conn
