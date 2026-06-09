-- Migration 011: background annotation jobs with polled progress.

CREATE TABLE IF NOT EXISTS annotation_jobs (
    id           TEXT PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK(status IN ('queued','running','completed','failed')),
    statement_id TEXT,
    total        INTEGER NOT NULL DEFAULT 0,
    processed    INTEGER NOT NULL DEFAULT 0,
    result       TEXT,   -- JSON AutoAnnotateResult once completed
    error        TEXT,
    created_at   TIMESTAMP DEFAULT (datetime('now')),
    updated_at   TIMESTAMP DEFAULT (datetime('now'))
);
