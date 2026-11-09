-- Migration 028: record which text each vector was built from.
-- A vector is current only when it comes from the active model AND from the
-- text build_embed_text produces today. Comparing a hash of that text (rather
-- than a global "embed format version") lets a rebuild skip rows that did not
-- change, resume where an interrupted rebuild stopped, and pick up rows whose
-- payee key or note moved because of a parser fix. Existing rows have no hash
-- and are rebuilt once.
ALTER TABLE embedding_meta ADD COLUMN text_hash TEXT;
