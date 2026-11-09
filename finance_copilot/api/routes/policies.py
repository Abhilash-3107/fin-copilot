"""Review policies (Settings > Rules > Policies): a standing answer to a whole
class of review questions. Every change re-decides what it can affect: the
waiting queue and the labels policies made."""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from finance_copilot.api.deps import get_db
from finance_copilot.db.queries.app_settings import get_confidence_threshold
from finance_copilot.db.queries.categories import CategoryError, require_category_ids
from finance_copilot.db.queries.review_policies import (
    SMALL_PERSONAL_PAYMENT,
    create_policy,
    delete_policy,
    list_policies,
    match_policy,
    update_policy,
)
from finance_copilot.pipeline.annotate import reevaluate_after_policy_change

router = APIRouter()


class PolicyBody(BaseModel):
    kind: Literal["small_personal_payment"] = SMALL_PERSONAL_PAYMENT
    max_amount: float = Field(gt=0)
    category: str
    subcategory: str | None = None


class PolicyPatch(BaseModel):
    max_amount: float | None = Field(default=None, gt=0)
    category: str | None = None
    subcategory: str | None = None
    enabled: bool | None = None


def _resolved(changed: list[dict]) -> int:
    return sum(c["resolved"] for c in changed)


@router.get("")
def list_all(conn: sqlite3.Connection = Depends(get_db)):
    return list_policies(conn)


@router.post("/preview")
def preview(body: PolicyBody, conn: sqlite3.Connection = Depends(get_db)):
    """How many transactions waiting for review this policy would sort now."""
    try:
        require_category_ids(conn, body.category, body.subcategory)
    except CategoryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    draft = [{**body.model_dump(), "enabled": True}]
    queued = conn.execute(
        "SELECT t.* FROM transactions t JOIN annotations a ON a.transaction_id = t.id "
        "WHERE a.source NOT IN ('manual', 'imported') AND a.confidence < ?",
        (get_confidence_threshold(conn),),
    ).fetchall()
    return {"matches": sum(match_policy(conn, draft, dict(r), as_of=None) is not None for r in queued)}


@router.post("", status_code=201)
def create(body: PolicyBody, conn: sqlite3.Connection = Depends(get_db)):
    try:
        policy = create_policy(conn, body.kind, body.max_amount, body.category, body.subcategory)
    except CategoryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    conn.commit()
    return {**policy, "resolved": _resolved(reevaluate_after_policy_change(conn))}


@router.patch("/{policy_id}")
def patch(policy_id: str, body: PolicyPatch, conn: sqlite3.Connection = Depends(get_db)):
    changes = body.model_dump(exclude_unset=True)
    if "subcategory" in changes and "category" not in changes:
        raise HTTPException(status_code=422, detail="Send the category with its subcategory")
    try:
        policy = update_policy(conn, policy_id, changes)
    except CategoryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    conn.commit()
    return {**policy, "resolved": _resolved(reevaluate_after_policy_change(conn))}


@router.delete("/{policy_id}")
def remove(policy_id: str, conn: sqlite3.Connection = Depends(get_db)):
    """Delete a policy; the labels it made are re-decided (back to review when
    nothing else settles them)."""
    if not delete_policy(conn, policy_id):
        raise HTTPException(status_code=404, detail="Policy not found")
    conn.commit()
    return {"returned_to_review": sum(not c["resolved"] for c in reevaluate_after_policy_change(conn))}
