-- Migration 002: upi_meta column on transactions + transaction group model

ALTER TABLE transactions ADD COLUMN upi_meta TEXT;

-- A named group that ties related transactions together (splits, reimbursements, etc.)
CREATE TABLE IF NOT EXISTS transaction_groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    group_type  TEXT NOT NULL CHECK(group_type IN ('split','reimbursement','refund','transfer','event')),
    note        TEXT,
    labels      TEXT,                               -- comma-separated, same pattern as annotation tags
    created_at  TIMESTAMP DEFAULT (datetime('now'))
);

-- Many-to-many: one transaction can belong to multiple groups
CREATE TABLE IF NOT EXISTS transaction_group_members (
    group_id        TEXT NOT NULL REFERENCES transaction_groups(id) ON DELETE CASCADE,
    transaction_id  TEXT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    role            TEXT CHECK(role IN ('paid','received','partial')),
    PRIMARY KEY (group_id, transaction_id)
);

CREATE INDEX IF NOT EXISTS idx_tgm_transaction ON transaction_group_members(transaction_id);
