"""Taxonomy hygiene: look-alike subcategories, orphaned names, write-path
snapping, and the unused-duplicate cleanup (findings/.../06)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.annotations import insert_annotation
from finance_copilot.db.queries.categories import CategoryError, merge_subcategory
from finance_copilot.db.queries.taxonomy import (
    canonical_subcategory,
    dismiss_duplicate,
    map_orphan_subcategory,
    similar_names,
    taxonomy_health,
)
from finance_copilot.models.annotation import Annotation

MIGRATIONS = Path(__file__).parents[1] / "db" / "migrations"


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_db(c)
    c.execute("INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s', 't', '1', '2026-06')")
    c.commit()
    return c


def _label(conn, txn_id, category_id, category, subcategory, subcategory_id):
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
        "VALUES (?, 's', '2026-06-01', 10, 'debit', 'x')", (txn_id,),
    )
    conn.execute(
        "INSERT INTO annotations (id, transaction_id, category, category_id, subcategory, subcategory_id, "
        "confidence, source) VALUES (?, ?, ?, ?, ?, ?, 1.0, 'manual')",
        (f"a_{txn_id}", txn_id, category, category_id, subcategory, subcategory_id),
    )


@pytest.mark.parametrize(("a", "b", "similar"), [
    ("Cafes", "Cafe & Snacks", True),
    ("Concerts", "Events & Concerts", True),
    ("Mutual Fund SIP", "mutual fund sips", True),
    ("Taxes", "Tax", True),
    ("Buses", "Bus", True),
    ("Kid's Toys", "Kids Toy", True),
    ("Restaurants", "Food Delivery", False),
    ("Flights", "Train", False),
])
def test_similar_names(a, b, similar):
    assert similar_names(a, b) is similar


class TestSuggestions:
    def test_the_name_the_catalog_writes_always_survives(self, conn):
        # 'Concerts' has more uses (and, in real ledgers, may even carry a
        # cat_* id), but the catalog writes 'Events & Concerts' by name.
        conn.execute("INSERT INTO categories (id, name, parent_id) VALUES ('u_concerts', 'Concerts', 'cat_entertainment')")
        for i in range(5):
            _label(conn, f"c{i}", "cat_entertainment", "Entertainment", "Concerts", "u_concerts")
        conn.commit()
        dup = next(d for d in taxonomy_health(conn)["duplicates"] if "u_concerts" in (d["keep"]["id"], d["merge"]["id"]))
        assert dup["keep"]["id"] == "cat_ent_events" and dup["merge"]["id"] == "u_concerts"

    def test_a_cluster_has_one_survivor(self, conn):
        conn.execute("INSERT INTO categories (id, name, parent_id) VALUES ('u1', 'Concerts', 'cat_entertainment')")
        conn.execute("INSERT INTO categories (id, name, parent_id) VALUES ('u2', 'Concerts & Shows', 'cat_entertainment')")
        conn.commit()
        dups = [d for d in taxonomy_health(conn)["duplicates"] if d["category"] == "Entertainment"]
        assert {d["keep"]["id"] for d in dups} == {"cat_ent_events"}
        assert {d["merge"]["id"] for d in dups} == {"u1", "u2"}

    def test_dismissed_pair_is_not_suggested_again(self, conn):
        conn.execute("INSERT INTO categories (id, name, parent_id) VALUES ('u_cafes', 'Cafes', 'cat_food_dining')")
        dismiss_duplicate(conn, "u_cafes", "cat_food_cafe")
        conn.commit()
        assert all(d["merge"]["id"] != "u_cafes" for d in taxonomy_health(conn)["duplicates"])

    def test_orphans_with_suggestion(self, conn):
        _label(conn, "t1", "cat_food_dining", "Food & Dining", "Restaurant", None)
        conn.commit()
        orphan = taxonomy_health(conn)["orphans"][0]
        assert (orphan["subcategory"], orphan["count"], orphan["suggestion"]["name"]) == ("Restaurant", 1, "Restaurants")

    def test_merge_follows_a_suggestion_and_the_catalog_still_resolves(self, conn):
        from finance_copilot.db.queries.categories import resolve_category_ids
        from finance_copilot.pipeline.merchant_catalog import MERCHANT_RULES

        conn.execute("INSERT INTO categories (id, name, parent_id) VALUES ('u_cafes', 'Cafes', 'cat_food_dining')")
        conn.commit()
        for d in taxonomy_health(conn)["duplicates"]:
            merge_subcategory(conn, d["merge"]["id"], d["keep"]["id"])
        for rule in MERCHANT_RULES:
            if rule.subcategory:
                assert resolve_category_ids(conn, rule.category, rule.subcategory)[1] is not None, rule


class TestWritePath:
    def test_machine_labels_snap_to_the_sibling_they_mean(self, conn):
        assert canonical_subcategory(conn, "cat_food_dining", "Restaurant") == ("Restaurants", "cat_food_restaurants")
        assert canonical_subcategory(conn, "cat_food_dining", "Dining") == (None, None)

    def test_insert_never_writes_an_orphan(self, conn):
        conn.execute("INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
                     "VALUES ('t', 's', '2026-06-01', 10, 'debit', 'x')")
        insert_annotation(conn, Annotation(transaction_id="t", category="Food & Dining", subcategory="Dining",
                                           confidence=0.5, source="llm"))
        row = conn.execute("SELECT subcategory, subcategory_id FROM annotations").fetchone()
        assert tuple(row) == (None, None)
        assert taxonomy_health(conn)["orphans"] == []


def test_map_orphan(conn):
    _label(conn, "t1", "cat_food_dining", "Food & Dining", "Dining", None)
    _label(conn, "t2", "cat_food_dining", "Food & Dining", "Dining", None)
    conn.commit()
    assert map_orphan_subcategory(conn, "cat_food_dining", "Dining", "cat_food_restaurants") == 2
    with pytest.raises(CategoryError):
        map_orphan_subcategory(conn, "cat_food_dining", "Dining", "cat_trans_cab")


class TestMigration:
    def _run(self, conn):
        conn.executescript((MIGRATIONS / "033_drop_unused_duplicate_subcategories.sql").read_text())

    def test_unused_copy_goes_used_copy_stays(self, conn):
        conn.execute("INSERT INTO categories (id, name, parent_id) VALUES ('legacy_sip', 'Mutual Fund SIP', 'cat_investments')")
        _label(conn, "t1", "cat_investments", "Investments", "Mutual Fund SIP", "legacy_sip")
        conn.commit()
        self._run(conn)
        assert [r[0] for r in conn.execute("SELECT id FROM categories WHERE name = 'Mutual Fund SIP'")] == ["legacy_sip"]

    def test_both_unused_keeps_the_built_in(self, conn):
        conn.execute("INSERT INTO categories (id, name, parent_id) VALUES ('01ULIDCOPY', 'Mutual Fund SIP', 'cat_investments')")
        conn.commit()
        self._run(conn)
        assert [r[0] for r in conn.execute("SELECT id FROM categories WHERE name = 'Mutual Fund SIP'")] == ["cat_inv_mf_sip"]

    def test_case_variants_and_look_alikes_are_left_to_the_user(self, conn):
        conn.execute("INSERT INTO categories (id, name, parent_id) VALUES ('u_case', 'mutual fund sip', 'cat_investments')")
        conn.execute("INSERT INTO categories (id, name, parent_id) VALUES ('u_cafes', 'Cafes', 'cat_food_dining')")
        conn.commit()
        self._run(conn)
        assert {r[0] for r in conn.execute("SELECT id FROM categories WHERE id IN ('u_case', 'u_cafes')")} == {
            "u_case", "u_cafes",
        }


class TestApi:
    @pytest.fixture
    def client(self, conn):
        from finance_copilot.api.deps import get_db as api_get_db
        from finance_copilot.main import app

        app.dependency_overrides[api_get_db] = lambda: conn
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_tidy_up_flow(self, client, conn):
        conn.execute("INSERT INTO categories (id, name, parent_id) VALUES ('u_cafes', 'Cafes', 'cat_food_dining')")
        _label(conn, "t1", "cat_food_dining", "Food & Dining", "Dining", None)
        conn.commit()
        body = client.get("/api/categories/tidy-up").json()
        assert [d["merge"]["id"] for d in body["duplicates"]] == ["u_cafes"]
        assert client.post("/api/categories/tidy-up/dismiss",
                           json={"keep_id": "cat_food_cafe", "merge_id": "u_cafes"}).status_code == 204
        assert client.get("/api/categories/tidy-up").json()["duplicates"] == []
        resp = client.post("/api/categories/tidy-up/orphans",
                           json={"category_id": "cat_food_dining", "name": "Dining", "target_id": "cat_food_restaurants"})
        assert resp.json() == {"moved": 1}
        bad = client.post("/api/categories/tidy-up/orphans",
                          json={"category_id": "cat_food_dining", "name": "Dining", "target_id": "cat_trans_cab"})
        assert bad.status_code == 422
