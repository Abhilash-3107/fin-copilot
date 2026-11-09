-- Migration 031: review policies.
-- A policy answers a whole class of review questions the user would otherwise
-- answer one transaction at a time. The first kind, 'small_personal_payment',
-- labels a UPI debit of at most max_amount to a payee that looks like a person,
-- is not on the People page, and has no verified history in another category
-- (e.g. "small payments to people are autos: Transport > Cab & Auto").
-- Kept apart from user_rules, which are keyword rules with preview/apply
-- semantics built around a pattern.
CREATE TABLE IF NOT EXISTS review_policies (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL CHECK(kind IN ('small_personal_payment')),
    max_amount      REAL NOT NULL CHECK(max_amount > 0),
    category_id     TEXT NOT NULL REFERENCES categories(id),
    subcategory_id  TEXT REFERENCES categories(id),
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMP DEFAULT (datetime('now')),
    updated_at      TIMESTAMP DEFAULT (datetime('now'))
);
