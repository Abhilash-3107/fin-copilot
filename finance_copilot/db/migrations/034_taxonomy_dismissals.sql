-- Migration 034: look-alike subcategory pairs the user said are meant to differ
-- ("Cafes" and "Cafe & Snacks" can be a deliberate distinction). The Tidy up
-- panel stops suggesting a dismissed pair. Ids stored in sorted order.
CREATE TABLE IF NOT EXISTS taxonomy_dismissals (
    a_id         TEXT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    b_id         TEXT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    dismissed_at TIMESTAMP DEFAULT (datetime('now')),
    PRIMARY KEY (a_id, b_id)
);
