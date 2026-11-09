"""Category routes: expose the two-level taxonomy and manage subcategories (4.13)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from finance_copilot.api.deps import get_db
from finance_copilot.db.queries.categories import (
    CategoryError,
    add_subcategory,
    get_category_tree,
    merge_subcategory,
    rename_subcategory,
)
from finance_copilot.db.queries.taxonomy import (
    dismiss_duplicate,
    map_orphan_subcategory,
    taxonomy_health,
)

router = APIRouter()


@router.get("")
def list_categories(conn: sqlite3.Connection = Depends(get_db)):
    """Return all categories as flat list: [{id, name, parent_id, color}]."""
    return get_category_tree(conn)


class AddSubcategory(BaseModel):
    parent_id: str
    name: str


class RenameSubcategory(BaseModel):
    name: str


class MergeSubcategory(BaseModel):
    target_id: str


class MapOrphan(BaseModel):
    category_id: str
    name: str
    target_id: str


@router.post("/subcategories", status_code=201)
def create_subcategory(body: AddSubcategory, conn: sqlite3.Connection = Depends(get_db)):
    """Add a subcategory under a top-level category."""
    try:
        created = add_subcategory(conn, body.parent_id, body.name)
    except CategoryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    conn.commit()
    return created


@router.patch("/subcategories/{category_id}")
def patch_subcategory(
    category_id: str, body: RenameSubcategory, conn: sqlite3.Connection = Depends(get_db)
):
    """Rename a subcategory; the new name cascades onto its annotations."""
    try:
        updated = rename_subcategory(conn, category_id, body.name)
    except CategoryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    conn.commit()
    return updated


@router.post("/subcategories/{category_id}/merge")
def merge_into(
    category_id: str, body: MergeSubcategory, conn: sqlite3.Connection = Depends(get_db)
):
    """Merge this subcategory into another (repoints annotations, then deletes it)."""
    try:
        result = merge_subcategory(conn, category_id, body.target_id)
    except CategoryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    conn.commit()
    return result


@router.get("/tidy-up")
def tidy_up(conn: sqlite3.Connection = Depends(get_db)):
    """Look-alike subcategories and orphaned subcategory names to resolve."""
    return taxonomy_health(conn)


class DismissPair(BaseModel):
    keep_id: str
    merge_id: str


@router.post("/tidy-up/dismiss", status_code=204)
def tidy_up_dismiss(body: DismissPair, conn: sqlite3.Connection = Depends(get_db)):
    """Two look-alike subcategories are meant to differ; stop suggesting the merge."""
    dismiss_duplicate(conn, body.keep_id, body.merge_id)
    conn.commit()


@router.post("/tidy-up/orphans")
def map_orphan(body: MapOrphan, conn: sqlite3.Connection = Depends(get_db)):
    """Move transactions labeled with an orphaned subcategory name onto a real one."""
    try:
        moved = map_orphan_subcategory(conn, body.category_id, body.name, body.target_id)
    except CategoryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    conn.commit()
    return {"moved": moved}
