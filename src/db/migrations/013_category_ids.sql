-- Migration 013: link annotations to the categories table by id.
-- Names stay denormalized for display; ids survive future renames and let us
-- validate on write.

-- Heal legacy rows written by old rules that used 'Financial', which never
-- existed in the taxonomy ('Finances' does; SIPs belong under 'Investments').
UPDATE annotations SET category = 'Investments'
WHERE category = 'Financial' AND subcategory = 'Mutual Fund SIP';
UPDATE annotations SET category = 'Finances'
WHERE category = 'Financial';

ALTER TABLE annotations ADD COLUMN category_id TEXT REFERENCES categories(id);
ALTER TABLE annotations ADD COLUMN subcategory_id TEXT REFERENCES categories(id);

UPDATE annotations SET category_id = (
    SELECT c.id FROM categories c
    WHERE c.name = annotations.category AND c.parent_id IS NULL
);

UPDATE annotations SET subcategory_id = (
    SELECT c.id FROM categories c
    WHERE c.name = annotations.subcategory AND c.parent_id = annotations.category_id
)
WHERE subcategory IS NOT NULL AND category_id IS NOT NULL;
