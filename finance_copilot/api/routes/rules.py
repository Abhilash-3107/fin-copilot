"""User-authored categorization rules (Settings > Rules > Your rules)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from finance_copilot.api.deps import get_db
from finance_copilot.db.queries.categories import CategoryError
from finance_copilot.db.queries.user_rules import (
    apply_rule_to_existing,
    create_user_rule,
    delete_user_rule,
    get_user_rule,
    list_user_rules,
    preview_rule,
    update_user_rule,
)

router = APIRouter()


class RuleBody(BaseModel):
    pattern: str
    category: str
    subcategory: str | None = None
    merchant: str | None = None


class RulePatch(BaseModel):
    pattern: str | None = None
    category: str | None = None
    subcategory: str | None = None
    merchant: str | None = None
    enabled: bool | None = None


@router.get("")
def list_all(conn: sqlite3.Connection = Depends(get_db)):
    return list_user_rules(conn)


@router.post("/preview")
def preview(body: RuleBody, conn: sqlite3.Connection = Depends(get_db)):
    """What this rule would match and change, before it's saved or applied."""
    try:
        return preview_rule(conn, body.pattern, body.category, body.subcategory)
    except CategoryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("", status_code=201)
def create(body: RuleBody, conn: sqlite3.Connection = Depends(get_db)):
    try:
        rule = create_user_rule(conn, body.pattern, body.category, body.subcategory, body.merchant)
    except CategoryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    conn.commit()
    return rule


@router.patch("/{rule_id}")
def patch(rule_id: str, body: RulePatch, conn: sqlite3.Connection = Depends(get_db)):
    changes = body.model_dump(exclude_unset=True)
    if "subcategory" in changes and "category" not in changes:
        # A subcategory only means something under its category; resend both.
        current = get_user_rule(conn, rule_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        changes["category"] = current["category"]
    try:
        rule = update_user_rule(conn, rule_id, changes)
    except CategoryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    conn.commit()
    return rule


@router.delete("/{rule_id}", status_code=204)
def remove(rule_id: str, conn: sqlite3.Connection = Depends(get_db)):
    if not delete_user_rule(conn, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    conn.commit()


@router.post("/{rule_id}/apply")
def apply(rule_id: str, background_tasks: BackgroundTasks, conn: sqlite3.Connection = Depends(get_db)):
    """Label the transactions this rule matches today. Labels a human set
    (manual, imported) are never touched."""
    from finance_copilot.api.routes.annotations import _embed_in_background

    rule = get_user_rule(conn, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    result = apply_rule_to_existing(conn, rule)
    conn.commit()
    touched = result.pop("transaction_ids")
    if touched:
        background_tasks.add_task(_embed_in_background, touched)
    return result
