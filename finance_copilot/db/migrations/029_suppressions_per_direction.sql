-- Migration 029: learned-rule dismissals are per payee *and* direction.
-- Learned rules became per (counterparty_key, direction): a merchant's payment
-- rule and its refund rule are separate, so dismissing one must not silently
-- kill the other. Existing dismissals predate the split and meant "stop this
-- payee", so they carry over for both directions.
CREATE TABLE learned_rule_suppressions_new (
    counterparty_key TEXT NOT NULL,
    direction        TEXT NOT NULL CHECK(direction IN ('debit', 'credit')),
    dismissed_at     TIMESTAMP DEFAULT (datetime('now')),
    PRIMARY KEY (counterparty_key, direction)
);

INSERT INTO learned_rule_suppressions_new (counterparty_key, direction, dismissed_at)
SELECT s.counterparty_key, d.direction, s.dismissed_at
FROM learned_rule_suppressions s
CROSS JOIN (SELECT 'debit' AS direction UNION ALL SELECT 'credit') d;

DROP TABLE learned_rule_suppressions;

ALTER TABLE learned_rule_suppressions_new RENAME TO learned_rule_suppressions;
