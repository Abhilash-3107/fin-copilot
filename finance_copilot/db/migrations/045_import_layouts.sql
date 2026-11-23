-- Migration 045: remembered spreadsheet layouts.
--
-- A CSV, XLSX or XLS whose columns Finance Copilot could not read from the header
-- is asked about once. The answer is kept here, keyed by a hash of the file's
-- normalised header row, together with the account the file went to, so the next
-- file with the same header maps itself and lands in the same account.
--
-- date_order is the person's answer for a date column that does not say which way
-- round it is ('dmy' or 'mdy'; NULL until they answer one), so the next file with
-- this header is read the same way without asking again.
--
-- account_id is ON DELETE SET NULL: an account with no statements is deleted
-- (db/queries/accounts.py), and the layout keeps what it learned about the
-- columns; only the account is asked for again.
CREATE TABLE import_layouts (
    signature   TEXT PRIMARY KEY,
    mapping     TEXT NOT NULL,
    account_id  TEXT REFERENCES accounts(id) ON DELETE SET NULL,
    date_order  TEXT CHECK (date_order IN ('dmy', 'mdy')),
    updated_at  TIMESTAMP NOT NULL DEFAULT (datetime('now'))
);
