-- Migration 023: pairwise transaction links (refund/split/reimbursement/transfer).
-- The table predates migration tracking, so long-lived databases already have
-- it; fresh installs were missing it entirely. Insights netting reads it.

CREATE TABLE IF NOT EXISTS transaction_links (
    id          TEXT PRIMARY KEY,
    txn_a       TEXT NOT NULL REFERENCES transactions(id),
    txn_b       TEXT NOT NULL REFERENCES transactions(id),
    link_type   TEXT NOT NULL CHECK(link_type IN ('split','reimbursement','refund','transfer')),
    note        TEXT,
    created_at  TIMESTAMP DEFAULT (datetime('now')),
    CHECK (txn_a < txn_b)
);

CREATE INDEX IF NOT EXISTS idx_links_a ON transaction_links(txn_a);
CREATE INDEX IF NOT EXISTS idx_links_b ON transaction_links(txn_b);
