-- Migration 041: accounts, and the period a statement prints.
--
-- An account is where money sits: identity is (bank, account number as printed).
-- The user's own name for it is optional and NULL until they set one; until then
-- the label is derived from the bank and the last digits.
--
-- Every existing statement is assigned an account with the rule the balance
-- chart used before accounts existed, so no ledger splinters:
--   * Within one bank (the " (auto)" marker the generic parser adds is not part
--     of the bank), if at most one distinct non-empty account number exists,
--     all of that bank's statements, unnumbered ones included, are one account
--     that carries the number.
--   * With two or more numbers, each number is an account, and the bank's
--     unnumbered statements are one more account without a number.
-- A bank_name the user edited by hand is not a canonical bank key; it is its own
-- bank, as stored.
--
-- Account ids derive from the oldest statement in the account, so the same
-- ledger migrates to the same ids every time.

CREATE TABLE accounts (
    id          TEXT PRIMARY KEY,
    bank_name   TEXT NOT NULL,
    account_ref TEXT,
    name        TEXT,
    created_at  TIMESTAMP DEFAULT (datetime('now'))
);

-- A number identifies an account within its bank. Unknown numbers (NULL) never
-- clash with each other, so any count of unnumbered accounts may exist.
CREATE UNIQUE INDEX idx_accounts_bank_ref ON accounts(bank_name, account_ref);

ALTER TABLE statements ADD COLUMN account_id TEXT REFERENCES accounts(id);
CREATE INDEX idx_statements_account ON statements(account_id);

-- The period the statement itself prints, as opposed to period_start/period_end,
-- which are the first and last payment dates. NULL when it could not be read.
ALTER TABLE statements ADD COLUMN printed_start DATE;
ALTER TABLE statements ADD COLUMN printed_end DATE;

CREATE TEMP TABLE _statement_account AS
WITH keyed AS (
    SELECT id,
           account_ref,
           CASE WHEN bank_name LIKE '% (auto)'
                THEN substr(bank_name, 1, length(bank_name) - 7)
                ELSE bank_name END AS bank
    FROM statements
),
numbers AS (
    SELECT bank, COUNT(DISTINCT account_ref) AS n, MIN(account_ref) AS only_ref
    FROM keyed
    WHERE account_ref IS NOT NULL AND account_ref != ''
    GROUP BY bank
)
SELECT k.id,
       k.bank,
       CASE
           WHEN k.account_ref IS NOT NULL AND k.account_ref != '' THEN k.account_ref
           WHEN n.n = 1 THEN n.only_ref
           ELSE NULL
       END AS ref
FROM keyed k
LEFT JOIN numbers n ON n.bank = k.bank;

INSERT INTO accounts (id, bank_name, account_ref)
SELECT 'acc_' || MIN(id), bank, ref
FROM _statement_account
GROUP BY bank, ref;

UPDATE statements SET account_id = (
    SELECT a.id
    FROM _statement_account g
    JOIN accounts a ON a.bank_name = g.bank AND a.account_ref IS g.ref
    WHERE g.id = statements.id
);

DROP TABLE _statement_account;
