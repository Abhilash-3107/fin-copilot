-- Migration 036: fill annotation category ids that were written as names only.
-- The demo seeder (0.1.2 and earlier) inserted annotations with a category and
-- subcategory name but no ids, so Settings > Categories reported every one of
-- them as an orphan and its Move fix could not apply. Names stay as they are;
-- an id is filled only when the name matches exactly one category row (a
-- subcategory: exactly one row under that category). Anything else, such as a
-- name the taxonomy does not have or one that two rows share, keeps its NULL.
-- Rows that already have an id are never touched, so re-running is a no-op.
UPDATE annotations SET category_id = (
    SELECT c.id FROM categories c
    WHERE c.name = annotations.category AND c.parent_id IS NULL
)
WHERE category_id IS NULL AND category IS NOT NULL
  AND (SELECT COUNT(*) FROM categories c
       WHERE c.name = annotations.category AND c.parent_id IS NULL) = 1;

UPDATE annotations SET subcategory_id = (
    SELECT c.id FROM categories c
    WHERE c.name = annotations.subcategory AND c.parent_id = annotations.category_id
)
WHERE subcategory_id IS NULL AND subcategory IS NOT NULL AND category_id IS NOT NULL
  AND (SELECT COUNT(*) FROM categories c
       WHERE c.name = annotations.subcategory AND c.parent_id = annotations.category_id) = 1;
