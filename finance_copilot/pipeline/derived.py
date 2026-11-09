"""Keep data derived from raw descriptions in step with the code that derives it.

Two things are computed from a transaction's raw_description and stored:
  - transactions.upi_meta and transactions.counterparty_key (parsers/description.py,
    pipeline/counterparty.py), versioned by DESCRIPTION_PARSER_VERSION;
  - the embedding vectors (pipeline/embed.py:build_embed_text), versioned by
    EMBED_TEXT_VERSION inside the embedding stamp.

When either version moves (a new bank layout, a parser fix), stored values are
stale. reconcile_derived_data runs at startup: it re-derives the parse in place
and reports whether vectors need a rebuild, so the next format fix ships as
"add grammar + fixtures + bump the version" with no manual migration script.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter

from finance_copilot.config import current_embedding_stamp, settings
from finance_copilot.parsers.description import (
    DESCRIPTION_PARSER_VERSION,
    ParsedDescription,
    is_known_shape,
    parse_description,
)
from finance_copilot.pipeline.counterparty import KeyResolver, identity_from_parsed

logger = logging.getLogger(__name__)

PARSER_VERSION_KEY = "description_parser_version"


def _stored_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (PARSER_VERSION_KEY,)).fetchone()
    try:
        return int(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def _merge_upi_meta(stored: str | None, parsed: ParsedDescription) -> str | None:
    """New upi_meta for a row: the parser overrides only what it understands.

    Structural fields (vpa, ref, bank, shape) always come from the parse. The
    note comes from the parse only for layouts the grammar fully understands;
    otherwise any stored note is kept (e.g. demo rows whose note is not part of
    the description).
    """
    if not parsed.is_upi:
        return stored
    try:
        meta = json.loads(stored) if stored else {}
        if not isinstance(meta, dict):
            meta = {}
    except json.JSONDecodeError:
        meta = {}
    meta.update({"vpa": parsed.vpa, "ref": parsed.ref, "bank": parsed.bank_code, "shape": parsed.shape})
    if is_known_shape(parsed) or "note" not in meta:
        meta["note"] = parsed.note
    return json.dumps(meta)


def rederive_descriptions(conn: sqlite3.Connection) -> dict:
    """Recompute upi_meta and resolved counterparty_key for every transaction.

    Rows are resolved in date order so the first-seen spelling of a payee stays
    canonical. Learned-rule suppressions are re-keyed through the old→new key
    map so a user's dismissal survives the re-derivation. Caller commits.
    """
    rows = conn.execute(
        "SELECT id, raw_description, upi_meta, counterparty_key FROM transactions "
        "ORDER BY txn_date, id"
    ).fetchall()
    resolver = KeyResolver()
    key_changes: dict[str, str | None] = {}
    shapes: Counter[str] = Counter()
    updated = 0
    for r in rows:
        parsed = parse_description(r["raw_description"], settings.upi_noise_keywords)
        new_key = resolver.resolve(identity_from_parsed(parsed))
        new_meta = _merge_upi_meta(r["upi_meta"], parsed)
        shapes[parsed.shape] += 1
        if new_key != r["counterparty_key"] or new_meta != r["upi_meta"]:
            conn.execute(
                "UPDATE transactions SET counterparty_key = ?, upi_meta = ? WHERE id = ?",
                (new_key, new_meta, r["id"]),
            )
            updated += 1
            if r["counterparty_key"] and new_key != r["counterparty_key"]:
                key_changes[r["counterparty_key"]] = new_key

    # A suppression is keyed by counterparty_key; follow keys that moved, but only
    # when every row of the old key moved to one new key (a split stays put).
    for old_key, new_key in key_changes.items():
        still_used = conn.execute(
            "SELECT 1 FROM transactions WHERE counterparty_key = ? LIMIT 1", (old_key,)
        ).fetchone()
        if new_key and not still_used:
            conn.execute(
                "UPDATE OR IGNORE learned_rule_suppressions SET counterparty_key = ? "
                "WHERE counterparty_key = ?",
                (new_key, old_key),
            )
    return {"rows": len(rows), "updated": updated, "keys_moved": len(key_changes), "shapes": dict(shapes)}


def embeddings_stale(conn: sqlite3.Connection) -> bool:
    """Whether stored vectors exist but none carry the current stamp (model +
    embed-text version), i.e. RAG retrieval is dark until a re-embed."""
    has_any = conn.execute("SELECT 1 FROM embedding_meta LIMIT 1").fetchone()
    if not has_any:
        return False
    current = conn.execute(
        "SELECT 1 FROM embedding_meta WHERE model_version = ? LIMIT 1", (current_embedding_stamp(),)
    ).fetchone()
    return current is None


def reconcile_derived_data(conn: sqlite3.Connection) -> bool:
    """Re-derive parsed description fields when the parser version moved.

    Returns True when stored vectors are stale and a re-embed should be
    scheduled. Commits its own work.
    """
    if _stored_version(conn) != DESCRIPTION_PARSER_VERSION:
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
    return embeddings_stale(conn)
