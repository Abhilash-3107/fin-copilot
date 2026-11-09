"""User-authored categorization rules: matching, precedence, preview, apply, routes."""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.annotations import get_annotation_by_transaction, insert_annotation
from finance_copilot.db.queries.user_rules import (
    UserRuleError,
    apply_rule_to_existing,
    create_user_rule,
    list_user_rules,
    match_user_rule,
    preview_rule,
)
from finance_copilot.models.annotation import Annotation
from finance_copilot.pipeline.annotate import rule_annotation


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_db(c)
    c.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','t','1','2026-01')"
    )
    return c


def _txn(conn, txn_id, desc, note=None, date="2026-01-10"):
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, upi_meta) "
        "VALUES (?, 's1', ?, 100.0, 'debit', ?, ?)",
        (txn_id, date, desc, json.dumps({"note": note}) if note else None),
    )
    return dict(conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone())


@pytest.fixture
def conn():
    c = _conn()
    yield c
    c.close()


class TestMatching:
    def test_word_bounded_and_case_insensitive(self, conn):
        create_user_rule(conn, "Cult", "Personal Care")
        rules = list_user_rules(conn)
        assert match_user_rule(rules, {"raw_description": "UPI/CULT.FIT/123"}) is not None
        assert match_user_rule(rules, {"raw_description": "UPI/CULTIVATE FARMS"}) is None

    def test_matches_the_upi_note_too(self, conn):
        create_user_rule(conn, "rent", "Housing")
        rules = list_user_rules(conn)
        assert match_user_rule(rules, {"raw_description": "UPI/AMIT", "upi_meta": json.dumps({"note": "Rent Nov"})})

    def test_most_specific_rule_wins(self, conn):
        create_user_rule(conn, "amazon", "Shopping")
        create_user_rule(conn, "amazon prime", "Entertainment")
        rules = list_user_rules(conn)
        assert match_user_rule(rules, {"raw_description": "AMAZON PRIME VIDEO"})["category"] == "Entertainment"
        assert match_user_rule(rules, {"raw_description": "AMAZON MARKETPLACE"})["category"] == "Shopping"

    def test_disabled_rules_never_match(self, conn):
        rule = create_user_rule(conn, "swiggy", "Shopping")
        conn.execute("UPDATE user_rules SET enabled = 0 WHERE id = ?", (rule["id"],))
        assert match_user_rule(list_user_rules(conn), {"raw_description": "SWIGGY"}) is None

    def test_unknown_category_rejected(self, conn):
        with pytest.raises(UserRuleError):
            create_user_rule(conn, "x", "Not A Category")
        with pytest.raises(UserRuleError):
            create_user_rule(conn, "x", "Shopping", "Not A Subcategory")
        with pytest.raises(UserRuleError):
            create_user_rule(conn, "   ", "Shopping")


class TestPipelinePrecedence:
    def test_user_rule_beats_built_in_rule(self, conn):
        # Built-in MERCHANT_RULES label Swiggy as Food Delivery; the user says
        # their Swiggy spend is Groceries (Instamart habit) - theirs wins.
        create_user_rule(conn, "swiggy", "Food & Dining", "Groceries")
        txn = _txn(conn, "t1", "UPI/SWIGGY/ORDER/123")
        result, trace = rule_annotation(conn, txn, [], as_of=None)
        assert (result.category, result.subcategory, result.source) == ("Food & Dining", "Groceries", "user_rule")
        assert trace.stage == "user_rule"
        assert "swiggy" in trace.matched_rule

    def test_user_rule_beats_known_person(self, conn):
        create_user_rule(conn, "sanya", "Entertainment")
        txn = _txn(conn, "t1", "UPI/SANYA PRASHANT/999/UPI")
        result, _ = rule_annotation(conn, txn, [("Sanya", "sanya", None)], as_of=None)
        assert result.category == "Entertainment"

    def test_without_rules_the_built_in_still_fires(self, conn):
        txn = _txn(conn, "t1", "UPI/SWIGGY/ORDER/123")
        result, _ = rule_annotation(conn, txn, [], as_of=None)
        assert (result.subcategory, result.source) == ("Food Delivery", "rule")

    def test_auto_annotate_persists_user_rule_source(self, conn):
        from finance_copilot.pipeline.annotate import auto_annotate

        create_user_rule(conn, "swiggy", "Food & Dining", "Groceries")
        _txn(conn, "t1", "UPI/SWIGGY/ORDER/123")
        conn.commit()
        result = auto_annotate(conn, transaction_ids=["t1"])
        assert result.rule_matched == 1
        ann = get_annotation_by_transaction(conn, "t1")
        assert (ann["source"], ann["subcategory"]) == ("user_rule", "Groceries")


class TestPreviewAndApply:
    def _seed(self, conn):
        _txn(conn, "auto_diff", "SWIGGY ORDER 1")
        _txn(conn, "auto_same", "SWIGGY ORDER 2")
        _txn(conn, "human", "SWIGGY ORDER 3")
        _txn(conn, "unlabeled", "SWIGGY ORDER 4")
        _txn(conn, "other", "ZOMATO ORDER")
        insert_annotation(conn, Annotation(transaction_id="auto_diff", category="Shopping", confidence=0.6, source="llm"))
        insert_annotation(conn, Annotation(transaction_id="auto_same", category="Food & Dining",
                                           subcategory="Groceries", confidence=0.9, source="rag_direct"))
        insert_annotation(conn, Annotation(transaction_id="human", category="Entertainment", confidence=1.0, source="manual"))
        conn.commit()

    def test_preview_buckets(self, conn):
        self._seed(conn)
        p = preview_rule(conn, "swiggy", "Food & Dining", "Groceries")
        assert {k: p[k] for k in ("matches", "relabel", "unlabeled", "unchanged", "protected")} == {
            "matches": 4, "relabel": 1, "unlabeled": 1, "unchanged": 1, "protected": 1,
        }
        assert {s["id"] for s in p["sample"]} <= {"auto_diff", "auto_same", "human", "unlabeled"}

    def test_apply_never_touches_human_labels(self, conn):
        self._seed(conn)
        rule = create_user_rule(conn, "swiggy", "Food & Dining", "Groceries")
        result = apply_rule_to_existing(conn, rule)
        conn.commit()
        assert sorted(result.pop("transaction_ids")) == ["auto_diff", "unlabeled"]
        assert result == {"relabeled": 1, "labeled": 1, "protected": 1, "unchanged": 1}
        assert get_annotation_by_transaction(conn, "human")["category"] == "Entertainment"
        relabeled = get_annotation_by_transaction(conn, "auto_diff")
        assert (relabeled["subcategory"], relabeled["source"], relabeled["original_source"]) == (
            "Groceries", "manual", "llm")
        assert get_annotation_by_transaction(conn, "unlabeled")["subcategory"] == "Groceries"
        assert get_annotation_by_transaction(conn, "other") is None


class TestTaxonomyFollow:
    def test_merging_a_subcategory_repoints_rules(self, conn):
        from finance_copilot.db.queries.categories import add_subcategory, merge_subcategory

        food = conn.execute("SELECT id FROM categories WHERE name='Food & Dining' AND parent_id IS NULL").fetchone()[0]
        extra = add_subcategory(conn, food, "Quick Commerce")
        groceries = conn.execute("SELECT id FROM categories WHERE name='Groceries' AND parent_id=?", (food,)).fetchone()[0]
        rule = create_user_rule(conn, "zepto", "Food & Dining", "Quick Commerce")
        merge_subcategory(conn, extra["id"], groceries)
        conn.commit()
        assert list_user_rules(conn)[0]["subcategory"] == "Groceries"
        assert rule["id"] == list_user_rules(conn)[0]["id"]


@pytest.fixture
def client_conn():
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.main import app

    c = _conn()
    app.dependency_overrides[api_get_db] = lambda: c
    with patch("finance_copilot.api.routes.annotations._embed_in_background") as bg:
        yield TestClient(app), c, bg
    app.dependency_overrides.pop(api_get_db, None)
    c.close()


class TestRoutes:
    def test_crud(self, client_conn):
        client, _, _ = client_conn
        r = client.post("/api/rules", json={"pattern": "  Cult  Fit ", "category": "Personal Care"})
        assert r.status_code == 201
        rule = r.json()
        assert rule["pattern"] == "cult fit" and rule["enabled"] is True
        assert client.get("/api/rules").json()[0]["id"] == rule["id"]

        r = client.patch(f"/api/rules/{rule['id']}", json={"enabled": False})
        assert r.json()["enabled"] is False
        r = client.patch(f"/api/rules/{rule['id']}", json={"category": "Shopping", "subcategory": "Online Shopping"})
        assert (r.json()["category"], r.json()["subcategory"]) == ("Shopping", "Online Shopping")

        assert client.delete(f"/api/rules/{rule['id']}").status_code == 204
        assert client.get("/api/rules").json() == []
        assert client.delete(f"/api/rules/{rule['id']}").status_code == 404

    def test_bad_category_is_422(self, client_conn):
        client, _, _ = client_conn
        assert client.post("/api/rules", json={"pattern": "x", "category": "Nope"}).status_code == 422
        assert client.post("/api/rules/preview", json={"pattern": "x", "category": "Nope"}).status_code == 422

    def test_preview_then_apply(self, client_conn):
        client, conn, bg = client_conn
        _txn(conn, "t1", "UPI/ZEPTO/1")
        _txn(conn, "t2", "UPI/ZEPTO/2")
        conn.commit()
        body = {"pattern": "zepto", "category": "Food & Dining", "subcategory": "Groceries"}
        p = client.post("/api/rules/preview", json=body).json()
        assert (p["matches"], p["unlabeled"]) == (2, 2)
        rule = client.post("/api/rules", json=body).json()
        r = client.post(f"/api/rules/{rule['id']}/apply").json()
        assert r == {"relabeled": 0, "labeled": 2, "protected": 0, "unchanged": 0}
        bg.assert_called_once()
        assert sorted(bg.call_args.args[0]) == ["t1", "t2"]

    def test_delete_everything_wipes_rules(self, client_conn):
        client, conn, _ = client_conn
        client.post("/api/rules", json={"pattern": "x", "category": "Shopping"})
        client.post("/api/data/delete-all", json={})
        assert conn.execute("SELECT COUNT(*) FROM user_rules").fetchone()[0] == 0
