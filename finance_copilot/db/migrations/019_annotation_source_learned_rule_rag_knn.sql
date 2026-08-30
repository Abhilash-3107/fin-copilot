-- Migration 019: expand annotation source CHECK to include learned_rule and rag_knn.
-- These sources shipped in the pipeline (stage 1.5 learned merchant memory, stage 2.5
-- trusted kNN vote) but the CHECK constraint was never widened, so their first insert
-- raised IntegrityError. SQLite cannot alter CHECK constraints so we recreate the
-- table preserving data, carrying the columns added by migrations 013-015.

CREATE TABLE IF NOT EXISTS annotations_new (
    id              TEXT PRIMARY KEY,
    transaction_id  TEXT NOT NULL UNIQUE REFERENCES transactions(id),
    merchant        TEXT,
    category        TEXT NOT NULL,
    subcategory     TEXT,
    tags            TEXT,
    confidence      REAL NOT NULL DEFAULT 0.0,
    source          TEXT NOT NULL CHECK(source IN ('manual','model','rule','learned_rule','rag_direct','rag_knn','rag_prompted','llm','imported')),
    annotated_at    TIMESTAMP DEFAULT (datetime('now')),
    original_source TEXT,
    category_id     TEXT REFERENCES categories(id),
    subcategory_id  TEXT REFERENCES categories(id),
    reasoning       TEXT
);

INSERT INTO annotations_new (id, transaction_id, merchant, category, subcategory, tags,
                             confidence, source, annotated_at, original_source,
                             category_id, subcategory_id, reasoning)
SELECT id, transaction_id, merchant, category, subcategory, tags,
       confidence, source, annotated_at, original_source,
       category_id, subcategory_id, reasoning
FROM annotations;

DROP TABLE annotations;

ALTER TABLE annotations_new RENAME TO annotations;

CREATE INDEX IF NOT EXISTS idx_annotations_category ON annotations(category);
