-- Migration 014: comma-separated TEXT lists → JSON arrays (JSON1).
-- Comma-joined values corrupt when an element contains a comma and can't be
-- queried; JSON arrays can (json_each) and round-trip cleanly.
-- The replace() trick is safe here: these columns were only ever written by
-- code that comma-joined simple slugs/ids, so elements contain no commas/quotes.

UPDATE annotations SET tags = CASE
    WHEN tags IS NULL OR tags = '' THEN '[]'
    WHEN json_valid(tags) AND json_type(tags) = 'array' THEN tags
    ELSE '["' || replace(tags, ',', '","') || '"]'
END;

UPDATE transaction_groups SET labels = CASE
    WHEN labels IS NULL OR labels = '' THEN NULL
    WHEN json_valid(labels) AND json_type(labels) = 'array' THEN labels
    ELSE '["' || replace(labels, ',', '","') || '"]'
END;

UPDATE transaction_group_members SET people = CASE
    WHEN people IS NULL OR people = '' THEN NULL
    WHEN json_valid(people) AND json_type(people) = 'array' THEN people
    ELSE '["' || replace(people, ',', '","') || '"]'
END;

UPDATE transaction_group_members SET labels = CASE
    WHEN labels IS NULL OR labels = '' THEN NULL
    WHEN json_valid(labels) AND json_type(labels) = 'array' THEN labels
    ELSE '["' || replace(labels, ',', '","') || '"]'
END;
