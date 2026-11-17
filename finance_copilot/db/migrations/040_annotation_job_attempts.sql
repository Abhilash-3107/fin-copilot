-- Migration 040: how many times a sorting job has been started.
-- A sort the app was closed in the middle of goes back in line at the next
-- start. Counting the starts lets the second interruption end it instead of
-- putting it back for ever (a crash while sorting one statement would
-- otherwise relaunch the same sort on every start and block delete, reset
-- and restore for good).

ALTER TABLE annotation_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
