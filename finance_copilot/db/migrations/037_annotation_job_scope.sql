-- Migration 037: a queued sorting job remembers which payments it covers.
-- Jobs now wait in the table for the job runner instead of in a thread, so the
-- scope a caller asked for (a list of transaction ids, JSON) has to be stored
-- with the row. NULL means the whole statement, or the whole ledger when the
-- statement is NULL too.

ALTER TABLE annotation_jobs ADD COLUMN transaction_ids TEXT;
