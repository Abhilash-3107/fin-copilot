-- Migration 035: a skip in the review queue belongs to the payment, not to its label.
-- Payments with no annotation (or none yet) now reach the queue, and they have no
-- annotation row to carry the snooze, so move the column to transactions.
ALTER TABLE transactions ADD COLUMN review_snoozed_at TIMESTAMP;

UPDATE transactions
SET review_snoozed_at = (
    SELECT a.review_snoozed_at FROM annotations a WHERE a.transaction_id = transactions.id
)
WHERE id IN (SELECT transaction_id FROM annotations WHERE review_snoozed_at IS NOT NULL);

DROP INDEX IF EXISTS idx_annotations_review_snoozed;
ALTER TABLE annotations DROP COLUMN review_snoozed_at;

-- Partial index over the skipped payments only, so finding them (to bring them
-- back) stays cheap. The queue's "review_snoozed_at IS NULL" filter is the
-- common case and does not use it.
CREATE INDEX IF NOT EXISTS idx_transactions_review_snoozed
    ON transactions(review_snoozed_at)
    WHERE review_snoozed_at IS NOT NULL;
