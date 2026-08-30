-- Migration 016: runtime app settings (key-value).

-- Small store for UI-toggleable settings that must survive restarts and be read
-- live by the backend (e.g. dev_mode, which gates annotation reasoning capture).
-- The DEV_MODE env var only seeds the initial default on first read.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT (datetime('now'))
);
