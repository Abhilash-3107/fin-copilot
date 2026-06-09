-- Migration 010: annotation provenance, statement dedup, and missing indexes.

-- Preserve the pipeline source (rule/rag_direct/rag_prompted/llm) when a human
-- edits or confirms an annotation and source flips to 'manual'.
ALTER TABLE annotations ADD COLUMN original_source TEXT;

-- Dedup statement uploads by file content. UNIQUE allows multiple NULLs, so
-- statements uploaded before this migration are unaffected.
ALTER TABLE statements ADD COLUMN file_sha256 TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_statements_file_sha256 ON statements(file_sha256);

CREATE INDEX IF NOT EXISTS idx_txn_statement ON transactions(statement_id);
CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(txn_date);
CREATE INDEX IF NOT EXISTS idx_annotations_category ON annotations(category);
