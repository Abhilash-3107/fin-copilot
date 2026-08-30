-- Migration 022: account identity for statements.
--
-- Balance continuity (021) only makes sense within one bank account. Statements
-- from different banks, or two accounts at the same bank, are independent
-- chains. account_ref holds the account number (or masked form) as printed on
-- the statement, extracted best-effort by the parser; NULL when the parser
-- cannot find one. The continuity check scopes to (bank_name, account_ref).

ALTER TABLE statements ADD COLUMN account_ref TEXT;
