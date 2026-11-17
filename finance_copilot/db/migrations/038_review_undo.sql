-- Migration 038: what a Teach Me answer changed, so the answer can be undone.
-- One row per answer: `changes` holds the annotation rows, snoozes and review
-- feedback counters as they were before and after. Only the last day and the
-- last 50 answers are kept (see db/queries/review_undo.py). A small precursor
-- of change sets, kept apart so it can be replaced.
CREATE TABLE IF NOT EXISTS review_undo (
    token      TEXT PRIMARY KEY,
    created_at TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    undone_at  TIMESTAMP,
    changes    TEXT NOT NULL
);
