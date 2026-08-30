-- Migration 012: statement period coverage (a Jan–Mar statement is no longer just "Jan").

ALTER TABLE statements ADD COLUMN period_start DATE;
ALTER TABLE statements ADD COLUMN period_end DATE;

-- Backfill from the transactions each statement actually contains.
UPDATE statements SET
    period_start = (SELECT MIN(txn_date) FROM transactions t WHERE t.statement_id = statements.id),
    period_end   = (SELECT MAX(txn_date) FROM transactions t WHERE t.statement_id = statements.id);
