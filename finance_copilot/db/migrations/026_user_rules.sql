-- Migration 026: user-authored categorization rules.
-- A rule is a keyword/phrase matched (word-bounded, case-insensitive) against a
-- transaction's description and UPI note, labelling it with a category. Rules
-- run first in stage 1, ahead of known-person matches, the built-in
-- MERCHANT_RULES, and learned merchant memory: what the user wrote on purpose
-- wins. Category/subcategory are stored by id only, so subcategory renames need
-- no cascade (merge_subcategory repoints them like annotations).

CREATE TABLE IF NOT EXISTS user_rules (
    id              TEXT PRIMARY KEY,
    pattern         TEXT NOT NULL,
    category_id     TEXT NOT NULL REFERENCES categories(id),
    subcategory_id  TEXT REFERENCES categories(id),
    merchant        TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMP DEFAULT (datetime('now')),
    updated_at      TIMESTAMP DEFAULT (datetime('now'))
);

-- Annotations produced by a user rule carry source 'user_rule'. SQLite can't
-- alter a CHECK constraint, so recreate the table as 019 did, carrying
-- review_snoozed_at (024) and both indexes.
CREATE TABLE IF NOT EXISTS annotations_new (
    id                TEXT PRIMARY KEY,
    transaction_id    TEXT NOT NULL UNIQUE REFERENCES transactions(id),
    merchant          TEXT,
    category          TEXT NOT NULL,
    subcategory       TEXT,
    tags              TEXT,
    confidence        REAL NOT NULL DEFAULT 0.0,
    source            TEXT NOT NULL CHECK(source IN ('manual','model','rule','user_rule','learned_rule','rag_direct','rag_knn','rag_prompted','llm','imported')),
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
