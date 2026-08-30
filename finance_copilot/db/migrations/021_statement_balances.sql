-- Migration 021: opening/closing balance are statement metadata, not transactions.
--
-- Kotak's Dec-Feb statement format prints "OPENING BALANCE" as a dated row with
-- an amount in the credit column, so the parser stored it as a credit
-- transaction. That inflates every credit aggregation ("Earned") by the whole
-- previous month's closing balance. The row is a property of the statement, so:
--
-- 1. statements gains opening_balance / closing_balance columns.
-- 2. Existing artifact rows are folded into statements.opening_balance and
--    deleted (with their annotations / embeddings / vectors).
-- 3. Interest rows the pipeline mislabelled as Opening Balance (e.g.
--    "Int.Pd:...Closing Balance") are real income; relabel as interest.
-- 4. Statements without an artifact row get balances computed from the
--    (parser-verified) running-balance chain: opening from the first row,
--    closing from the last. rowid order within a statement is parse order.

ALTER TABLE statements ADD COLUMN opening_balance REAL;
ALTER TABLE statements ADD COLUMN closing_balance REAL;

UPDATE statements SET opening_balance = (
    SELECT COALESCE(t.running_balance, t.amount)
    FROM transactions t
    WHERE t.statement_id = statements.id
      AND UPPER(t.raw_description) LIKE 'OPENING BALANCE%'
    ORDER BY t.rowid LIMIT 1
);

DELETE FROM annotations WHERE transaction_id IN (
    SELECT id FROM transactions WHERE UPPER(raw_description) LIKE 'OPENING BALANCE%');
DELETE FROM embedding_meta WHERE transaction_id IN (
    SELECT id FROM transactions WHERE UPPER(raw_description) LIKE 'OPENING BALANCE%');
DELETE FROM vec_items WHERE transaction_id IN (
    SELECT id FROM transactions WHERE UPPER(raw_description) LIKE 'OPENING BALANCE%');
DELETE FROM transactions WHERE UPPER(raw_description) LIKE 'OPENING BALANCE%';

-- Interest-paid lines are genuine income, not balance artifacts. The seeded
-- Interest & Dividends subcategory is the correct home.
UPDATE annotations SET
    subcategory    = 'Interest & Dividends',
    subcategory_id = 'cat_inc_interest'
WHERE subcategory = 'Opening Balance'
  AND transaction_id IN (
    SELECT id FROM transactions WHERE UPPER(raw_description) LIKE 'INT.PD%');

UPDATE statements SET opening_balance = (
    SELECT t.running_balance
           + CASE WHEN t.debit_credit = 'debit' THEN t.amount ELSE -t.amount END
    FROM transactions t
    WHERE t.statement_id = statements.id AND t.running_balance IS NOT NULL
    ORDER BY t.rowid LIMIT 1
) WHERE opening_balance IS NULL;

UPDATE statements SET closing_balance = (
    SELECT t.running_balance
    FROM transactions t
    WHERE t.statement_id = statements.id AND t.running_balance IS NOT NULL
    ORDER BY t.rowid DESC LIMIT 1
);
