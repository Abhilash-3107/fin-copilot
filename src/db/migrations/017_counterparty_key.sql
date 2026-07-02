-- Counterparty identity as a first-class indexed column.
-- counterparty_history() previously scanned every UPI annotation and re-derived
-- the identity in Python per transaction: O(total annotations) per call. The key
-- is now computed once at ingest (and backfilled here via the same rule:
-- upper-cased, whitespace-collapsed 2nd '/'-segment of a 'UPI/...' description)
-- and looked up through an index.
ALTER TABLE transactions ADD COLUMN counterparty_key TEXT;

-- Backfill: SQLite has no split(); the 2nd segment is the text between the 1st
-- and 2nd '/'. Whitespace-collapse of interior runs is rare in practice and is
-- normalized on the Python side for new rows; TRIM covers the observed data.
UPDATE transactions
SET counterparty_key = UPPER(TRIM(substr(
        substr(raw_description, instr(raw_description, '/') + 1),
        1,
        instr(substr(raw_description, instr(raw_description, '/') + 1), '/') - 1
    )))
WHERE raw_description LIKE 'UPI/%/%'
  AND counterparty_key IS NULL;

-- Empty-name edge case → NULL, matching normalize_identity().
UPDATE transactions SET counterparty_key = NULL WHERE counterparty_key = '';

CREATE INDEX IF NOT EXISTS idx_transactions_counterparty_key
    ON transactions (counterparty_key)
    WHERE counterparty_key IS NOT NULL;
