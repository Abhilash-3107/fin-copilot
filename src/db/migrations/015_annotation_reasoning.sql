-- Migration 015: dev-mode annotation reasoning trace.

-- Stores a JSON ReasoningTrace (neighbours, similarity math, donor vote, raw vs
-- dampened confidence, and the LLM's one-sentence "why") captured at annotation
-- time. Only written when DEV_MODE is on; NULL for all existing and regular rows.
ALTER TABLE annotations ADD COLUMN reasoning TEXT;
