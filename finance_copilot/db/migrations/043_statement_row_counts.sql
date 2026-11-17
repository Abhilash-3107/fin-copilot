-- Migration 043: what a statement's file held, and whether its balance checked out.
--
-- An overlapping statement adds only the payments the account does not have yet
-- (pipeline/dedup.py), so a statement's payments are no longer all of its file.
-- Each statement now records, when it is added:
--   rows_in_file        how many payments its file held
--   rows_added          how many of them were new, and so are its payments
--   balance_checked     the running-balance steps of the whole file, as parsed
--   balance_consistent  how many of those steps reconcile with their amounts
-- The balance check is the file's, not the stored rows': the rows a later file
-- added are not a chain, and must not make it look unbalanced.
--
-- Existing statements brought every payment of their file, so both counts are
-- their payments, and the check is the one the statement list showed before
-- (consecutive payments that carry a running balance, in the order stored,
-- within a cent), so a statement that was balanced stays balanced and one that
-- was not stays flagged.

ALTER TABLE statements ADD COLUMN rows_in_file INTEGER;
ALTER TABLE statements ADD COLUMN rows_added INTEGER;
ALTER TABLE statements ADD COLUMN balance_checked INTEGER;
ALTER TABLE statements ADD COLUMN balance_consistent INTEGER;

UPDATE statements SET
    rows_in_file = (SELECT COUNT(*) FROM transactions t WHERE t.statement_id = statements.id),
    rows_added = (SELECT COUNT(*) FROM transactions t WHERE t.statement_id = statements.id);

CREATE TEMP TABLE _balance_check AS
WITH chain AS (
    SELECT statement_id, running_balance, amount, debit_credit,
           LAG(running_balance) OVER (PARTITION BY statement_id ORDER BY rowid) AS prev_balance
    FROM transactions
    WHERE running_balance IS NOT NULL
)
SELECT statement_id,
       COUNT(*) AS checked,
       SUM(CASE WHEN ABS((running_balance - prev_balance)
                         - (CASE WHEN debit_credit = 'credit' THEN amount ELSE -amount END)) <= 0.01
                THEN 1 ELSE 0 END) AS consistent
FROM chain
WHERE prev_balance IS NOT NULL AND debit_credit IS NOT NULL
GROUP BY statement_id;

UPDATE statements SET
    balance_checked = COALESCE((SELECT c.checked FROM _balance_check c WHERE c.statement_id = statements.id), 0),
    balance_consistent = COALESCE((SELECT c.consistent FROM _balance_check c WHERE c.statement_id = statements.id), 0);

DROP TABLE _balance_check;
