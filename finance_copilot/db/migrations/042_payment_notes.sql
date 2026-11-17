-- Migration 042: a note the user writes on a payment.
-- transactions.user_note replaces the UPI note wherever a row shows its note;
-- clearing it brings the UPI note back. Like a payment name (migration 037) it
-- is a label: raw_description, upi_meta, counterparty_key, categories, learned
-- rules, matching, embeddings and the model never read it. The resolution lives
-- in db/queries/payee.py (with_payee).
ALTER TABLE transactions ADD COLUMN user_note TEXT;
