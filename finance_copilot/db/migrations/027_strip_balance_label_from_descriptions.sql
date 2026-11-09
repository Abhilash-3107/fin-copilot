-- Migration 027: drop the statement's balance label from transaction descriptions.
-- The Kotak parser read the closing-balance label into the description cell of
-- each statement's last transaction ("UPI/ZOMATO/122406278840/UPI Closing
-- Balance"), which corrupted that row's UPI note, embedding, and identity. The
-- parser now strips it on ingest; this repairs rows stored before the fix. Only
-- a trailing label after other text is removed, never a standalone balance row.
-- Derived fields (upi_meta, counterparty_key, vectors) are recomputed by the
-- startup re-derivation in pipeline/derived.py.
UPDATE transactions
SET raw_description = RTRIM(SUBSTR(raw_description, 1, LENGTH(raw_description) - LENGTH(' Closing Balance')))
WHERE raw_description LIKE '_% Closing Balance';

UPDATE transactions
SET raw_description = RTRIM(SUBSTR(raw_description, 1, LENGTH(raw_description) - LENGTH(' Opening Balance')))
WHERE raw_description LIKE '_% Opening Balance';
