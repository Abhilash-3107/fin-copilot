-- Initial migration: statements, transactions, annotations, categories, embedding_meta,
-- and sqlite-vec virtual table vec_items. Implement DDL exactly as specified in plan.md.

-- Tracks each uploaded bank statement PDF
CREATE TABLE IF NOT EXISTS statements (
    id              TEXT PRIMARY KEY,
    bank_name       TEXT NOT NULL,
    parser_version  TEXT NOT NULL,
    statement_month TEXT NOT NULL,
    uploaded_at     TIMESTAMP DEFAULT (datetime('now'))
);

-- One row per transaction extracted from a statement
CREATE TABLE IF NOT EXISTS transactions (
    id              TEXT PRIMARY KEY,
    statement_id    TEXT NOT NULL REFERENCES statements(id),
    txn_date        DATE NOT NULL,
    amount          REAL NOT NULL,
    debit_credit    TEXT NOT NULL CHECK(debit_credit IN ('debit','credit')),
    raw_description TEXT NOT NULL,
    running_balance REAL
);

-- Annotation for a transaction (one-to-one, written separately)
CREATE TABLE IF NOT EXISTS annotations (
    id              TEXT PRIMARY KEY,
    transaction_id  TEXT NOT NULL UNIQUE REFERENCES transactions(id),
    merchant        TEXT,
    category        TEXT NOT NULL,
    subcategory     TEXT,
    tags            TEXT,
    confidence      REAL NOT NULL DEFAULT 0.0,
    source          TEXT NOT NULL CHECK(source IN ('manual','model','imported')),
    annotated_at    TIMESTAMP DEFAULT (datetime('now'))
);

-- Two-level category hierarchy (parent_id NULL = top-level)
CREATE TABLE IF NOT EXISTS categories (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    parent_id TEXT REFERENCES categories(id),
    color     TEXT
);

-- Embedding metadata (actual vectors in sqlite-vec virtual table)
CREATE TABLE IF NOT EXISTS embedding_meta (
    id              TEXT PRIMARY KEY,
    transaction_id  TEXT NOT NULL UNIQUE REFERENCES transactions(id),
    model_version   TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT (datetime('now'))
);

-- sqlite-vec virtual table (run after loading the extension)
CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(
    transaction_id TEXT PRIMARY KEY,
    embedding      FLOAT[768]
);
