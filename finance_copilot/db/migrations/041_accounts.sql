-- Migration 041: accounts, and the period a statement prints.
--
-- An account is where money sits: identity is (bank, account number as printed).
-- The user's own name for it is optional and NULL until they set one; until then
-- the label is derived from the bank and the last digits.
--
-- Every existing statement is assigned an account with the rule the balance
-- chart used before accounts existed, so no ledger splinters:
--   * Within one bank, if at most one distinct non-empty account number exists,
--     all of that bank's statements, unnumbered ones included, are one account
--     that carries the number.
--   * With two or more numbers, each number is an account, and the bank's
--     unnumbered statements are one more account without a number.
--
-- A bank is the statement's bank_name trimmed, lower case and without the
-- generic parser's " (auto)" marker (accounts.bank_key does the same in Python).
--
-- The old statement rename wrote what the user typed into bank_name ("Kotak
-- salary"). Text that is not a bank key a parser reports:
--   * on a statement whose account number is the number of a real bank's
--     statements, belongs to that bank: it joins that bank's account;
--   * otherwise is its own bank, as typed.
-- Either way the typed text becomes the account's name (the latest statement's
-- text when several differ), so the screens keep showing what the user wrote.
-- A blank bank_name has no text to keep and reads "Bank".
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

-- Not unique: the user can split one number into two accounts by hand (move a
-- statement to a new account), and ingest then follows where they put the last
-- statement of that bank and number (db/queries/accounts.py).
CREATE INDEX idx_accounts_bank_ref ON accounts(bank_name, account_ref);

ALTER TABLE statements ADD COLUMN account_id TEXT REFERENCES accounts(id);
CREATE INDEX idx_statements_account ON statements(account_id);

-- The period the statement itself prints, as opposed to period_start/period_end,
-- which are the first and last payment dates. NULL when it could not be read.
ALTER TABLE statements ADD COLUMN printed_start DATE;
ALTER TABLE statements ADD COLUMN printed_end DATE;

-- One row per statement: its bank key, its number (NULL when empty), and the
-- text the user typed when the bank_name is not a bank key.
CREATE TEMP TABLE _stmt AS
WITH trimmed AS (
    SELECT id, statement_month, uploaded_at,
           NULLIF(trim(account_ref, ' ' || char(9) || char(10) || char(13)), '') AS ref,
           trim(bank_name, ' ' || char(9) || char(10) || char(13)) AS raw
    FROM statements
),
unmarked AS (
    SELECT id, statement_month, uploaded_at, ref,
           CASE WHEN lower(raw) LIKE '%(auto)'
                THEN trim(substr(raw, 1, length(raw) - 6), ' ' || char(9) || char(10) || char(13))
                ELSE raw END AS typed
    FROM trimmed
)
SELECT id, statement_month, uploaded_at, ref, typed, lower(typed) AS bank,
       lower(typed) IN (
           'kotak', 'hdfc', 'icici', 'sbi', 'axis', 'yes bank', 'idfc', 'indusind',
           'punjab national', 'canara', 'union bank', 'bank of baroda', 'federal',
           'rbl', 'au small finance', 'generic'
       ) AS is_bank
FROM unmarked;

-- Typed text on a number a real bank's statements carry joins that bank.
CREATE TEMP TABLE _placed AS
SELECT s.id, s.statement_month, s.uploaded_at, s.ref,
       COALESCE(
           CASE WHEN s.is_bank = 0 AND s.ref IS NOT NULL THEN
               (SELECT c.bank FROM _stmt c WHERE c.is_bank = 1 AND c.ref = s.ref
                ORDER BY c.statement_month DESC, c.uploaded_at DESC, c.id DESC LIMIT 1)
           END,
           s.bank) AS bank,
       CASE WHEN s.is_bank = 0 AND s.typed != '' THEN s.typed END AS typed
FROM _stmt s;

CREATE TEMP TABLE _statement_account AS
WITH numbers AS (
    SELECT bank, COUNT(DISTINCT ref) AS n, MIN(ref) AS only_ref
    FROM _placed
    WHERE ref IS NOT NULL
    GROUP BY bank
)
SELECT p.id, p.bank, p.statement_month, p.uploaded_at, p.typed,
       CASE
           WHEN p.ref IS NOT NULL THEN p.ref
           WHEN n.n = 1 THEN n.only_ref
           ELSE NULL
       END AS ref
FROM _placed p
LEFT JOIN numbers n ON n.bank = p.bank;

INSERT INTO accounts (id, bank_name, account_ref, name)
SELECT 'acc_' || MIN(g.id), g.bank, g.ref,
       (SELECT x.typed FROM _statement_account x
         WHERE x.bank = g.bank AND x.ref IS g.ref AND x.typed IS NOT NULL
         ORDER BY x.statement_month DESC, x.uploaded_at DESC, x.id DESC LIMIT 1)
FROM _statement_account g
GROUP BY g.bank, g.ref;

UPDATE statements SET account_id = (
    SELECT a.id
    FROM _statement_account g
    JOIN accounts a ON a.bank_name = g.bank AND a.account_ref IS g.ref
    WHERE g.id = statements.id
);

DROP TABLE _statement_account;
DROP TABLE _placed;
DROP TABLE _stmt;
