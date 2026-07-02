-- Migration 020: two related bits of merchant-memory hygiene.
--
-- 1. people.relationship — how the user knows this person (dad, mom, friend, …).
--    A subset of relationships (dad/mom/sister/wife/…) map internally to the
--    Family subcategory so the stage-1 person rule can label those transfers as
--    Transfers › Family instead of the generic Peer Transfer. Nullable: an
--    unlabelled person keeps the old Peer Transfer behaviour.
--
-- 2. learned_rule_suppressions — a user's explicit "stop showing / applying this
--    learned merchant rule". Learned rules are computed live from verified labels
--    (no stored table), so suppression is the one bit of user state that overrides
--    them. Keyed by counterparty_key (the same identity lookup_learned_rule uses).
--    Sticky: re-verifying the counterparty does not clear it; the user restores it
--    explicitly. If a suppressed rule visibly re-learns, that surfaces a bug.

ALTER TABLE people ADD COLUMN relationship TEXT;

-- Family is a first-class Transfers subcategory now that the person rule emits it
-- for family-labelled contacts. (Until now it only existed as free-text a user
-- happened to type when verifying a relative's transfers.)
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_tx_family', 'Family', 'cat_transfers');

CREATE TABLE IF NOT EXISTS learned_rule_suppressions (
    counterparty_key TEXT PRIMARY KEY,
    dismissed_at     TIMESTAMP DEFAULT (datetime('now'))
);
