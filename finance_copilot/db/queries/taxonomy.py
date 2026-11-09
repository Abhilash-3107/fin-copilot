"""Taxonomy hygiene (findings/2026-11-09-pipeline-robustness/06).

Duplicate or orphaned subcategories split one kind of spending across labels,
which weakens learned-rule purity, the RAG vote, and every insight grouped by
subcategory. Two halves:
  - prevention: machine writes snap an unknown subcategory name to the one
    sibling it plainly means ('Restaurant' -> 'Restaurants') or drop it, so
    orphans don't accumulate (canonical_subcategory);
  - repair: Settings > Categories lists look-alike siblings and orphaned names
    for the user to resolve (taxonomy_health). Merging is a taxonomy choice
    ('Cafes' vs 'Cafe & Snacks' may be deliberate), so nothing merges on its
    own, and a dismissed pair stays dismissed.
"""
from __future__ import annotations

import re
import sqlite3

from finance_copilot.db.queries.categories import CategoryError

_CONNECTORS = frozenset({"and", "the", "of", "n"})


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def name_tokens(name: str) -> frozenset[str]:
    """Words of a subcategory name, normalized for comparison: case, '&'/'and',
    apostrophes, punctuation, and plurals ('Kid's Toys' ~ 'kids toy')."""
    text = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace("'", "").replace("&", " and "))
    return frozenset(_singular(w) for w in text.split() if w not in _CONNECTORS)


def similar_names(a: str, b: str) -> bool:
    """Same words, or one name's words all inside the other's ('Concerts' in
    'Events & Concerts'). Over-eager by design: it only feeds suggestions the
    user confirms or dismisses."""
    ta, tb = name_tokens(a), name_tokens(b)
    return bool(ta and tb) and (ta <= tb or tb <= ta)


def canonical_subcategory(conn: sqlite3.Connection, category_id: str | None, name: str | None) -> tuple[str | None, str | None]:
    """(name, id) of the subcategory a machine-written name means: the exact
    sibling, else the one sibling with the same normalized words, else none."""
    if category_id is None or not name:
        return None, None
    siblings = conn.execute("SELECT id, name FROM categories WHERE parent_id = ?", (category_id,)).fetchall()
    exact = [s for s in siblings if s["name"] == name]
    same = exact or [s for s in siblings if name_tokens(s["name"]) == name_tokens(name)]
    return (same[0]["name"], same[0]["id"]) if len(same) == 1 else (None, None)


def _subcategories(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    uses = dict(conn.execute(
        "SELECT subcategory_id, COUNT(*) FROM annotations WHERE subcategory_id IS NOT NULL GROUP BY 1"
    ).fetchall())
    by_parent: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT id, name, parent_id FROM categories WHERE parent_id IS NOT NULL ORDER BY id"):
        by_parent.setdefault(r["parent_id"], []).append({**dict(r), "uses": uses.get(r["id"], 0)})
    return by_parent


def _catalog_names() -> frozenset[tuple[str, str]]:
    """(category, subcategory) names the built-in merchant catalog writes."""
    from finance_copilot.pipeline.merchant_catalog import DISAMBIGUATION_RULES, MERCHANT_RULES

    return frozenset((r.category, r.subcategory) for r in [*MERCHANT_RULES, *DISAMBIGUATION_RULES] if r.subcategory)


def _survivor_order(sub: dict, category: str, catalog: frozenset[tuple[str, str]]) -> tuple:
    # A subcategory the merchant catalog writes by name must survive: merging
    # it away would orphan every future catalog label. Then the most used.
    return ((category, sub["name"]) not in catalog, -sub["uses"], sub["id"])


def taxonomy_health(conn: sqlite3.Connection) -> dict:
    """Look-alike siblings, as clusters with one survivor each, and orphaned
    annotation subcategory names, each with a suggested fix."""
    names = dict(conn.execute("SELECT id, name FROM categories WHERE parent_id IS NULL").fetchall())
    dismissed = {frozenset(r) for r in conn.execute("SELECT a_id, b_id FROM taxonomy_dismissals")}
    catalog = _catalog_names()
    duplicates = []
    for parent_id, subs in _subcategories(conn).items():
        cluster_of = {s["id"]: {s["id"]} for s in subs}
        for i, a in enumerate(subs):
            for b in subs[i + 1:]:
                if frozenset((a["id"], b["id"])) not in dismissed and similar_names(a["name"], b["name"]):
                    merged = cluster_of[a["id"]] | cluster_of[b["id"]]
                    for sid in merged:
                        cluster_of[sid] = merged
        by_id = {s["id"]: s for s in subs}
        for cluster in {frozenset(c) for c in cluster_of.values() if len(c) > 1}:
            keep, *rest = sorted((by_id[i] for i in cluster), key=lambda sub: _survivor_order(sub, names[parent_id], catalog))
            duplicates += [{
                "category": names[parent_id],
                "keep": {k: keep[k] for k in ("id", "name", "uses")},
                "merge": {k: m[k] for k in ("id", "name", "uses")},
                "same_name": keep["name"] == m["name"],
            } for m in rest]

    siblings = _subcategories(conn)
    orphans = []
    # A category name always resolves: every writer resolves it against the
    # taxonomy (the LLM's answer is normalized to the category list first), so
    # only subcategory names can be orphaned.
    for r in conn.execute(
        """
        SELECT category, category_id, subcategory, COUNT(*) AS n FROM annotations
        WHERE subcategory IS NOT NULL AND subcategory != '' AND subcategory_id IS NULL
        GROUP BY category_id, subcategory ORDER BY n DESC
        """
    ):
        suggestion = next(
            (o for o in siblings.get(r["category_id"], []) if similar_names(r["subcategory"], o["name"])), None
        )
        orphans.append({
            "category": r["category"], "category_id": r["category_id"], "subcategory": r["subcategory"],
            "count": r["n"], "suggestion": {"id": suggestion["id"], "name": suggestion["name"]} if suggestion else None,
        })
    return {"duplicates": duplicates, "orphans": orphans}


def dismiss_duplicate(conn: sqlite3.Connection, a_id: str, b_id: str) -> None:
    """Remember that two look-alike subcategories are meant to differ. Caller commits."""
    first, second = sorted((a_id, b_id))
    conn.execute("INSERT OR IGNORE INTO taxonomy_dismissals (a_id, b_id) VALUES (?, ?)", (first, second))


def map_orphan_subcategory(conn: sqlite3.Connection, category_id: str, name: str, target_id: str) -> int:
    """Point every annotation carrying an orphaned subcategory name at a real
    subcategory of the same category. Returns rows moved. Caller commits."""
    target = conn.execute("SELECT name, parent_id FROM categories WHERE id = ?", (target_id,)).fetchone()
    if target is None or target["parent_id"] != category_id:
        raise CategoryError("Pick a subcategory of the same category")
    return conn.execute(
        "UPDATE annotations SET subcategory = ?, subcategory_id = ? "
        "WHERE category_id = ? AND subcategory = ? AND subcategory_id IS NULL",
        (target["name"], target_id, category_id, name),
    ).rowcount
