-- Tracks per-(source, category) human feedback counts for Bayesian confidence calibration.
-- One row per (source, category) pair. Used by src/pipeline/calibration.py to compute
-- dynamic dampening factors that replace the static llm_confidence_dampen settings.
CREATE TABLE IF NOT EXISTS feedback_stats (
    source      TEXT NOT NULL,
    category    TEXT NOT NULL,
    confirmed   INTEGER NOT NULL DEFAULT 0,
    refined     INTEGER NOT NULL DEFAULT 0,
    corrected   INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMP DEFAULT (datetime('now')),
    PRIMARY KEY (source, category)
);
