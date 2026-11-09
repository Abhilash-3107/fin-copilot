-- Migration 033: remove unused exact-duplicate subcategories.
-- Some long-lived databases carry two subcategories with the same name under
-- one parent (e.g. a legacy "Mutual Fund SIP" id next to the seeded one). The
-- name is all the user sees, so no choice can tell them apart: drop a copy
-- nothing uses (no annotation, user rule, or review policy). The survivor is
-- the used copy; between unused ones, the built-in (cat_*) one, the same rule
-- the Tidy up panel uses, since the catalog and prompts name built-ins.
-- Exact name match: names resolve case-sensitively everywhere else.
DELETE FROM categories
WHERE parent_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM annotations a WHERE a.subcategory_id = categories.id)
  AND NOT EXISTS (SELECT 1 FROM user_rules r WHERE r.subcategory_id = categories.id)
  AND NOT EXISTS (SELECT 1 FROM review_policies p WHERE p.subcategory_id = categories.id)
  AND EXISTS (
      SELECT 1 FROM categories d
      WHERE d.parent_id = categories.parent_id
        AND d.name = categories.name
        AND d.id != categories.id
        AND (
            EXISTS (SELECT 1 FROM annotations a WHERE a.subcategory_id = d.id)
            OR EXISTS (SELECT 1 FROM user_rules r WHERE r.subcategory_id = d.id)
            OR EXISTS (SELECT 1 FROM review_policies p WHERE p.subcategory_id = d.id)
            OR (d.id LIKE 'cat\_%' ESCAPE '\' AND categories.id NOT LIKE 'cat\_%' ESCAPE '\')
            OR ((d.id LIKE 'cat\_%' ESCAPE '\') = (categories.id LIKE 'cat\_%' ESCAPE '\') AND d.id < categories.id)
        )
  );
