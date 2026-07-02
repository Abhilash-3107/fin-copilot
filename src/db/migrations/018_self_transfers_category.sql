-- Promote "Self Transfer" from a subcategory of Transfers to its own top-level
-- category "Self Transfers".
--
-- Self-transfers move money between the user's own accounts — the money stays
-- with them, so it is neither spend nor income and should not roll up under
-- Transfers (which is peer/outgoing). Keeping it as its own top-level category
-- lets spending views exclude it cleanly.

-- 1. New top-level category.
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_self_transfers', 'Self Transfers', NULL);

-- 2. Reparent the existing "Self Transfer" subcategory under it (keep the id so
--    existing annotations' subcategory_id stays valid).
UPDATE categories SET parent_id = 'cat_self_transfers' WHERE id = 'cat_tx_self';

-- 3. Migrate existing annotations: category Transfers/Self Transfer → the new
--    top-level. subcategory (and its id, cat_tx_self) are unchanged and now
--    correctly resolve under Self Transfers.
UPDATE annotations
SET category = 'Self Transfers', category_id = 'cat_self_transfers'
WHERE category = 'Transfers' AND subcategory = 'Self Transfer';
