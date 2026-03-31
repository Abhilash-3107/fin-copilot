"""Category routes: expose the two-level taxonomy."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from src.api.deps import get_db
from src.db.queries.categories import get_category_tree

router = APIRouter()


@router.get("")
def list_categories(conn: sqlite3.Connection = Depends(get_db)):
    """Return all categories as flat list: [{id, name, parent_id, color}]."""
    return get_category_tree(conn)
