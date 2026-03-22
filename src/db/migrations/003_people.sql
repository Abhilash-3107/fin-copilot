-- Migration 003: people/contacts table + people + labels columns on group members

CREATE TABLE IF NOT EXISTS people (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL,
    upi   TEXT    -- optional UPI handle e.g. rahul@upi
);

-- comma-separated people IDs involved in this (group, transaction) membership
ALTER TABLE transaction_group_members ADD COLUMN people TEXT;

-- per-membership labels (replaces group-level labels for per-transaction context)
ALTER TABLE transaction_group_members ADD COLUMN labels TEXT;
