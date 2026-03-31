-- Migration 007: expand annotation source to include rule, rag_direct, rag_prompted, llm.
-- SQLite cannot alter CHECK constraints so we recreate the table preserving data.
-- 'model' is kept for backward compatibility with existing rows.

CREATE TABLE IF NOT EXISTS annotations_new (
    id              TEXT PRIMARY KEY,
    transaction_id  TEXT NOT NULL UNIQUE REFERENCES transactions(id),
    merchant        TEXT,
    category        TEXT NOT NULL,
    subcategory     TEXT,
    tags            TEXT,
    confidence      REAL NOT NULL DEFAULT 0.0,
    source          TEXT NOT NULL CHECK(source IN ('manual','model','rule','rag_direct','rag_prompted','llm','imported')),
    annotated_at    TIMESTAMP DEFAULT (datetime('now'))
);

INSERT INTO annotations_new SELECT * FROM annotations;

DROP TABLE annotations;

ALTER TABLE annotations_new RENAME TO annotations;
