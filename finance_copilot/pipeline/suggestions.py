"""Suggestions for the review queue: up to three labels per item, each with the
reason it is offered, best first.

Sources, in order of preference:
  1. what the user chose before for this payee (verified labels, with a count),
  2. the item's own machine guess, with a plain reason from its source,
  3. the label of the most similar verified payment, when embeddings exist.
A payment that could be one side of a transfer between the user's own accounts
(pipeline/pairing.py found a candidate it was not sure enough to pair) is offered
first, once per candidate, as a suggestion that pairs the two payments.
Uncategorized is never offered. History and neighbour labels are read in a few
queries for the whole page, never per item, except one vector search per item
that still has room for a suggestion.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from datetime import date

from finance_copilot.config import settings
from finance_copilot.db.queries.common import UNCATEGORIZED
from finance_copilot.db.queries.embeddings import find_similar, stored_embeddings
from finance_copilot.db.queries.transfers import SELF_CATEGORY, SELF_SUBCATEGORY
from finance_copilot.parsers.description import parse_description
from finance_copilot.pipeline.annotate import _is_evidence
from finance_copilot.pipeline.pairing import transfer_candidates
from finance_copilot.pipeline.sources import VERIFIED_SOURCES

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3
# At most this many candidate pairs per card, so the usual suggestions keep a place.
MAX_TRANSFER_SUGGESTIONS = 2
_CHUNK = 500
_VERIFIED = sorted(VERIFIED_SOURCES)

Label = tuple[str, str | None]  # (category, subcategory)


def _chunks(values: list[str]):
    for i in range(0, len(values), _CHUNK):
        yield values[i : i + _CHUNK]


def _label(category: str | None, subcategory: str | None) -> Label | None:
    """A real label, or None: no category, or Uncategorized, is not an answer."""
    if not category or category == UNCATEGORIZED:
        return None
    return category, subcategory or None


def _times(n: int) -> str:
    return "once" if n == 1 else f"{n} times"


def _verified_history(conn: sqlite3.Connection, items: list[dict]) -> dict[tuple[str, str], Counter]:
    """{(payee key, direction): Counter of label -> how often the user chose it}."""
    keys = sorted({it["counterparty_key"] for it in items if it["counterparty_key"]})
    wanted = {(it["counterparty_key"], it["debit_credit"]) for it in items if it["counterparty_key"]}
    history: dict[tuple[str, str], Counter] = {}
    for chunk in _chunks(keys):
        rows = conn.execute(
            f"""
            SELECT t.counterparty_key AS key, t.debit_credit AS dc, a.category, a.subcategory, COUNT(*) AS n
            FROM annotations a JOIN transactions t ON t.id = a.transaction_id
            WHERE t.counterparty_key IN ({",".join("?" * len(chunk))})
              AND a.source IN ({",".join("?" * len(_VERIFIED))}) AND a.category != ?
            GROUP BY t.counterparty_key, t.debit_credit, a.category, a.subcategory
            """,
            (*chunk, *_VERIFIED, UNCATEGORIZED),
        )
        for row in rows:
            if (row["key"], row["dc"]) in wanted and (label := _label(row["category"], row["subcategory"])):
                history.setdefault((row["key"], row["dc"]), Counter())[label] += row["n"]
    return history


def _guess_reason(item: dict) -> str:
    source = item["source"]
    if source in ("llm", "model", "rag_prompted"):
        # rag_prompted is a model's answer given similar payments as context: still a guess.
        return f"AI guess, {round((item['confidence'] or 0) * 100)}% sure"
    if source == "rag_direct":
        # The number of payments it copied from is only kept in the dev-mode trace.
        return "Similar to past payments"
    if source == "learned_rule":
        return "Remembered payee"
    if source == "policy":
        return "Your rule for small payments"
    if source == "user_rule":
        return "Your rule"
    if source == "imported":
        return "Imported label"
    return "Current label"


def _similar_labels(conn: sqlite3.Connection, items: list[dict]) -> dict[str, list[tuple[Label, str]]]:
    """{transaction_id: [(label, reason)]} from the verified payments nearest to
    each item, for the items given. Quietly empty without embeddings."""
    try:
        vectors = stored_embeddings(conn, [it["transaction_id"] for it in items])
        found = {
            it["transaction_id"]: [
                m["transaction_id"]
                for m in find_similar(
                    conn, vectors[it["transaction_id"]], top_k=settings.rag_top_k,
                    exclude_transaction_ids=[it["transaction_id"]],
                )
                if _is_evidence(m)
            ]
            for it in items if it["transaction_id"] in vectors
        }
    except sqlite3.OperationalError as e:
        if "no such" not in str(e):  # anything but "no embeddings here" is worth a look
            logger.warning("suggestions | similar payments unavailable | %s", e)
        else:
            logger.debug("suggestions | no embeddings | %s", e)
        return {}
    except Exception as e:
        logger.warning("suggestions | similar payments unavailable | %s", e)
        return {}

    neighbour_ids = sorted({n for ids in found.values() for n in ids})
    neighbours: dict[str, dict] = {}
    for chunk in _chunks(neighbour_ids):
        for row in conn.execute(
            f"""
            SELECT t.id, t.raw_description, t.debit_credit, a.category, a.subcategory
            FROM annotations a JOIN transactions t ON t.id = a.transaction_id
            WHERE t.id IN ({",".join("?" * len(chunk))})
              AND a.source IN ({",".join("?" * len(_VERIFIED))}) AND a.category != ?
            """,
            (*chunk, *_VERIFIED, UNCATEGORIZED),
        ):
            neighbours[row["id"]] = dict(row)

    out: dict[str, list[tuple[Label, str]]] = {}
    for it in items:
        labels = []
        for nid in found.get(it["transaction_id"], []):
            n = neighbours.get(nid)
            if n is None or n["debit_credit"] != it["debit_credit"]:
                continue
            label = _label(n["category"], n["subcategory"])
            payee = parse_description(n["raw_description"]).counterparty or "a past payment"
            if label:
                labels.append((label, f"Similar to {payee}, which you sorted this way"))
        out[it["transaction_id"]] = labels
    return out


def _gap(days: int) -> str:
    return "the same day" if days == 0 else f"{days} day{'' if days == 1 else 's'} apart"


def _transfer_suggestion(found: dict) -> dict:
    day = date.fromisoformat(found["txn_date"])
    reason = f"Same amount, {_gap(found['gap_days'])}. Pairing keeps both out of spending."
    if found["matches"] > 1:
        reason = f"One of {found['matches']} possible matches. {reason}"
    return {
        "category": SELF_CATEGORY,
        "subcategory": SELF_SUBCATEGORY,
        "reason": reason,
        "pair_with": found["transaction_id"],
        "title": f"Moved {found['direction']} {found['account_label']}, {day.day} {day:%b}",
    }


def _add_transfer_suggestions(conn: sqlite3.Connection, items: list[dict]) -> None:
    """Put each card's transfer candidates ahead of its usual suggestions, within
    the cap. The card's own guess keeps a place, as it does without candidates."""
    found = transfer_candidates(conn, [it["transaction_id"] for it in items])
    for it in items:
        candidates = found.get(it["transaction_id"], [])[:MAX_TRANSFER_SUGGESTIONS]
        if not candidates:
            continue
        picks = [_transfer_suggestion(c) for c in candidates]
        usual = it["suggestions"]
        merged = (picks + usual)[:MAX_SUGGESTIONS]
        guess = _label(it["category"], it["subcategory"])
        own = next((s for s in usual if guess and (s["category"], s["subcategory"]) == guess), None)
        if own is not None and own not in merged:
            merged[-1] = own
        it["suggestions"] = merged


def attach_suggestions(conn: sqlite3.Connection, items: list[dict]) -> None:
    """Add `suggestions` ([{category, subcategory, reason}], at most three) to
    every queue item, in place."""
    history = _verified_history(conn, items)

    ranked: dict[str, dict[Label, str]] = {}
    guess_of: dict[str, Label | None] = {}
    for it in items:
        reasons: dict[Label, str] = {}
        counts = history.get((it["counterparty_key"], it["debit_credit"]), Counter())
        payee = it.get("payee_all") or it.get("payee") or "this payee"
        for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1] or "")):
            reasons[label] = f"You chose this {_times(n)} for {payee}"
        guess = _label(it["category"], it["subcategory"])
        guess_of[it["transaction_id"]] = guess
        if guess and guess not in reasons:
            reasons[guess] = _guess_reason(it)
        ranked[it["transaction_id"]] = reasons

    roomy = [it for it in items if len(ranked[it["transaction_id"]]) < MAX_SUGGESTIONS]
    for tid, labels in _similar_labels(conn, roomy).items():
        for label, reason in labels:
            ranked[tid].setdefault(label, reason)

    for it in items:
        tid = it["transaction_id"]
        top = list(ranked[tid].items())[:MAX_SUGGESTIONS]
        guess = guess_of[tid]
        # The card's own guess keeps a place even behind three well-supported labels.
        if guess and all(label != guess for label, _ in top):
            top[-1] = (guess, ranked[tid][guess])
        it["suggestions"] = [
            {"category": label[0], "subcategory": label[1], "reason": reason} for label, reason in top
        ]
    _add_transfer_suggestions(conn, items)
