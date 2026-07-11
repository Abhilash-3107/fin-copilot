-- 4.15: skipping in the review queue was a dead end - client-only state, so a
-- skipped item silently reappeared on the next load. Persist a snooze timestamp
-- so a skip sticks across sessions; the queue excludes snoozed rows, and a
-- correction (update_annotation) or an explicit unsnooze clears it.
ALTER TABLE annotations ADD COLUMN review_snoozed_at TIMESTAMP;

-- Partial index so the review-queue's "WHERE review_snoozed_at IS NULL" filter
-- and the snoozed-count query stay cheap as the corpus grows.
CREATE INDEX IF NOT EXISTS idx_annotations_review_snoozed
    ON annotations(review_snoozed_at)
    WHERE review_snoozed_at IS NOT NULL;
