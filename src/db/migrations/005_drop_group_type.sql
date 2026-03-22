-- Migration 005: remove group_type from transaction_groups
-- txn_type on transaction_group_members is now the authoritative type per transaction

ALTER TABLE transaction_groups DROP COLUMN group_type;
