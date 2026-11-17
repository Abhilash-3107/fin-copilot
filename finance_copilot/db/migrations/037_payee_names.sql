-- Migration 037: names the user gives to payees and to single payments.
-- A payee name labels every payment with one counterparty_key, past and future.
-- A payment name (transactions.display_name) labels that payment only.
-- Both are labels: raw_description, counterparty_key, categories, learned rules
-- and matching never read them. The resolution order lives in
-- db/queries/payee.py: payment name, payee name, annotation merchant, parsed
-- payee, printed line.
CREATE TABLE IF NOT EXISTS payee_names (
    counterparty_key TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    updated_at       TIMESTAMP DEFAULT (datetime('now'))
);

ALTER TABLE transactions ADD COLUMN display_name TEXT;
