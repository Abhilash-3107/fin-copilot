-- Migration 004: move transaction type from group level to per-membership level
-- group_type on transaction_groups becomes optional (a hint/default only)
-- txn_type on transaction_group_members is the authoritative type per transaction

ALTER TABLE transaction_group_members ADD COLUMN txn_type TEXT
    CHECK(txn_type IN ('split','reimbursement','refund','transfer','event'));
