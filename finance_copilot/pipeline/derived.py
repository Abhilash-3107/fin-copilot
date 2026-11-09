"""Keep data derived from raw descriptions in step with the code that derives it.

transactions.upi_meta and transactions.counterparty_key are computed from
raw_description (parsers/description.py, pipeline/counterparty.py) and stored.
When DESCRIPTION_PARSER_VERSION moves (a new bank layout, a parser fix) or a
migration repairs raw descriptions (it clears the stored version), startup
re-derives both for every transaction. Vectors follow on their own: each
records a hash of the text it was built from (pipeline/embed.py), so rows whose
embed text changed are rebuilt by the startup refresh.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import Counter

from finance_copilot.config import settings
from finance_copilot.parsers.description import DESCRIPTION_PARSER_VERSION, parse_description
from finance_copilot.parsers.upi import upi_meta_json
from finance_copilot.pipeline.counterparty import canonical_keys, identity_from_parsed

logger = logging.getLogger(__name__)

PARSER_VERSION_KEY = "description_parser_version"


def rederive_descriptions(conn: sqlite3.Connection) -> dict:
    """Recompute upi_meta and canonical counterparty_key for every transaction.

    Learned-rule suppressions follow a key only when all of its rows moved to
    one new key; a key that split keeps its suppression where it was. Caller
    commits.
    """
    rows = conn.execute("SELECT id, raw_description, upi_meta, counterparty_key FROM transactions").fetchall()
    derived = []
    for r in rows:
        parsed = parse_description(r["raw_description"], settings.upi_noise_keywords)
        derived.append((r, identity_from_parsed(parsed), upi_meta_json(parsed), parsed.shape))
    canonical = canonical_keys({raw for _r, raw, _m, _s in derived if raw is not None})

    moved: dict[str, set[str | None]] = {}
    updated = 0
    for r, raw, meta, _shape in derived:
        key = canonical[raw] if raw is not None else None
        if r["counterparty_key"] is not None:
            moved.setdefault(r["counterparty_key"], set()).add(key)
        if key != r["counterparty_key"] or meta != r["upi_meta"]:
            conn.execute(
                "UPDATE transactions SET counterparty_key = ?, upi_meta = ? WHERE id = ?", (key, meta, r["id"])
            )
            updated += 1

    for old_key, new_keys in moved.items():
        if len(new_keys) == 1 and (new_key := next(iter(new_keys))) not in (None, old_key):
            conn.execute(
                "UPDATE OR IGNORE learned_rule_suppressions SET counterparty_key = ? WHERE counterparty_key = ?",
                (new_key, old_key),
            )
    return {
        "rows": len(rows),
        "updated": updated,
        "keys_moved": sum(1 for old, new in moved.items() if new != {old}),
        "shapes": dict(Counter(shape for *_rest, shape in derived)),
    }


def reconcile_derived_data(conn: sqlite3.Connection) -> None:
    """Re-derive parsed description fields when the stored parser version is
    missing or differs. Commits."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (PARSER_VERSION_KEY,)).fetchone()
    if row is not None and int(row[0]) == DESCRIPTION_PARSER_VERSION:
        return
    result = rederive_descriptions(conn)
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')",
        (PARSER_VERSION_KEY, str(DESCRIPTION_PARSER_VERSION)),
    )
    conn.commit()
    logger.info(
        "description parser v%d: re-derived %d/%d transactions, %d counterparty keys moved | shapes=%s",
        DESCRIPTION_PARSER_VERSION, result["updated"], result["rows"], result["keys_moved"], result["shapes"],
    )
