-- Migration 044: a transfer between the user's own accounts is paired, not spent.
--
-- Pairing links a debit in one account with the matching credit in another
-- (transaction_links, link_type 'transfer') and sorts both sides as Self
-- Transfers with a new annotation source, 'pair': a fact two statements agree
-- on, not a guess and not the user's own label. SQLite can't alter a CHECK
-- constraint, so the annotations table is recreated as 032 did, with the
-- columns it has now (035 moved the snooze to transactions).
--
-- transfer_unpaired remembers the pairs the user took apart, so pairing never
-- puts them back.
CREATE TABLE annotations_new (
    id                TEXT PRIMARY KEY,
    transaction_id    TEXT NOT NULL UNIQUE REFERENCES transactions(id),
    merchant          TEXT,
    category          TEXT NOT NULL,
    subcategory       TEXT,
    tags              TEXT,
    confidence        REAL NOT NULL DEFAULT 0.0,
    source            TEXT NOT NULL CHECK(source IN ('manual','model','rule','user_rule','learned_rule','rag_direct','rag_knn','rag_prompted','llm','imported','policy','pair')),
    annotated_at      TIMESTAMP DEFAULT (datetime('now')),
    original_source   TEXT,
    category_id       TEXT REFERENCES categories(id),
    subcategory_id    TEXT REFERENCES categories(id),
    reasoning         TEXT
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

CREATE TABLE transfer_unpaired (
    txn_a      TEXT NOT NULL,
    txn_b      TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT (datetime('now')),
    PRIMARY KEY (txn_a, txn_b),
    CHECK (txn_a < txn_b)
);
