"""Canonical annotation-source taxonomy.

Every annotation carries a `source` naming the stage that produced it. Several
subsystems partition those sources three ways, and before this module each kept
its own copy of the list — they had already drifted ("model" appearing in some
lists but not others). This module is the single source of truth; it imports
nothing from the pipeline so any layer can depend on it without a cycle.

- TRUSTED: labels trustworthy enough to seed learning at full weight (human
  edits, deterministic rules, imports). Machine guesses are downweighted so a
  recurring mislabel can't bootstrap its own bad prior.
- MACHINE: machine-produced labels eligible to be overwritten by "apply to
  similar" (a human decision propagates onto them). Never includes human sources.
- MODEL: sources whose outcomes feed calibration. Corrections/confirmations to
  any of these tune thresholds, even where only some are dampened today.
"""
from __future__ import annotations

# Deterministic or human-verified; full weight in every learning loop.
TRUSTED_SOURCES: frozenset[str] = frozenset({"manual", "rule", "imported"})

# Machine-produced labels that "apply to similar" may overwrite.
MACHINE_SOURCES: frozenset[str] = frozenset({"llm", "rag_prompted", "rag_direct", "learned_rule"})

# Sources whose corrections/confirmations are recorded as calibration feedback.
# rule and learned_rule ride along so their thresholds and bad donors surface too.
MODEL_SOURCES: frozenset[str] = MACHINE_SOURCES | {"rule"}

# Sources that can appear in the low-confidence review queue. Includes the legacy
# "model" value from pre-split annotations still present in long-lived databases.
REVIEWABLE_SOURCES: frozenset[str] = MODEL_SOURCES | {"model"}
