-- Migration 032: annotations produced by a review policy carry source 'policy'.
-- A policy is the user's rule of thumb ("small payments to people are autos"),
-- not a fact about the payee, so its labels must stay machine labels: re-decided
-- when policies change and never trusted as RAG donors or learned-rule evidence.
-- SQLite can't alter a CHECK constraint, so recreate the table as 026 did.
CREATE TABLE IF NOT EXISTS annotations_new (
    id                TEXT PRIMARY KEY,
    transaction_id    TEXT NOT NULL UNIQUE REFERENCES transactions(id),
    merchant          TEXT,
    category          TEXT NOT NULL,
    subcategory       TEXT,
    tags              TEXT,
    confidence        REAL NOT NULL DEFAULT 0.0,
    source            TEXT NOT NULL CHECK(source IN ('manual','model','rule','user_rule','learned_rule','rag_direct','rag_knn','rag_prompted','llm','imported','policy')),
    annotated_at      TIMESTAMP DEFAULT (datetime('now')),
    original_source   TEXT,
    category_id       TEXT REFERENCES categories(id),
    subcategory_id    TEXT REFERENCES categories(id),
    reasoning         TEXT,
    review_snoozed_at TIMESTAMP
);

INSERT INTO annotations_new (id, transaction_id, merchant, category, subcategory, tags,
                             confidence, source, annotated_at, original_source,
                             category_id, subcategory_id, reasoning, review_snoozed_at)
SELECT id, transaction_id, merchant, category, subcategory, tags,
       confidence, source, annotated_at, original_source,
       category_id, subcategory_id, reasoning, review_snoozed_at
FROM annotations;

DROP TABLE annotations;

ALTER TABLE annotations_new RENAME TO annotations;

CREATE INDEX IF NOT EXISTS idx_annotations_category ON annotations(category);

CREATE INDEX IF NOT EXISTS idx_annotations_review_snoozed
    ON annotations(review_snoozed_at)
    WHERE review_snoozed_at IS NOT NULL;
