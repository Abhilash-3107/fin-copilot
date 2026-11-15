"""POST /api/data/demo: seed the bundled demo ledger, never into a real one."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finance_copilot.db.connection import init_db


@pytest.fixture
def client_conn():
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.main import app

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    app.dependency_overrides[api_get_db] = lambda: conn
    yield TestClient(app), conn
    app.dependency_overrides.pop(api_get_db, None)
    conn.close()


class TestLoadDemoData:
    def test_seeds_fresh_db(self, client_conn):
        client, conn = client_conn
        r = client.post("/api/data/demo")
        assert r.status_code == 200
        body = r.json()
        assert body["transactions"] > 100
        assert body["months"] == 4
        assert body["review_backlog"] > 0
        txns = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert txns == body["transactions"]

    def test_second_call_is_idempotent(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        first = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        r = client.post("/api/data/demo")
        assert r.status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == first

    def test_goa_group_tells_a_settled_story(self, client_conn):
        # The demo group's note says both friends settled; settle-up must agree,
        # and every tag must be a real person id the UI can resolve to a name.
        client, conn = client_conn
        client.post("/api/data/demo")
        detail = client.get("/api/groups/demo_grp_goa").json()
        assert detail["summary"]["settle_up"] == []
        tagged = {p for m in detail["members"] for p in (m["people"] or [])}
        names = {r[0] for r in conn.execute(
            f"SELECT name FROM people WHERE id IN ({','.join('?' * len(tagged))})", list(tagged))}
        assert names == {"Rahul Mehta", "Neha Gupta"}

    def test_demo_rule_fires_on_the_unannotated_batch(self, client_conn):
        # The live auto-annotate demo should show a user rule deciding first.
        from finance_copilot.pipeline.annotate import rule_annotation

        client, conn = client_conn
        client.post("/api/data/demo")
        rules = client.get("/api/rules").json()
        assert [r["pattern"] for r in rules] == ["bmrcl"]
        metro = conn.execute(
            "SELECT t.* FROM transactions t LEFT JOIN annotations a ON a.transaction_id = t.id "
            "WHERE t.raw_description LIKE '%BMRCL%' AND a.id IS NULL"
        ).fetchone()
        result, _ = rule_annotation(conn, dict(metro), [], as_of=None)
        assert (result.source, result.subcategory) == ("user_rule", "Public Transport")

    def test_refused_when_real_statements_exist(self, client_conn):
        client, conn = client_conn
        conn.execute(
            "INSERT INTO statements (id, bank_name, parser_version, statement_month) "
            "VALUES ('real1', 'kotak', '1', '2026-05')"
        )
        r = client.post("/api/data/demo")
        assert r.status_code == 409
        assert "real statements" in r.json()["detail"]


MIGRATIONS = Path(__file__).parents[1] / "db" / "migrations"


class TestSeededCategoryIds:
    def test_a_fresh_demo_ledger_has_nothing_to_tidy(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        health = client.get("/api/categories/tidy-up").json()
        assert health["orphans"] == []
        assert health["duplicates"] == []

    def test_every_named_category_carries_its_id(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        missing = conn.execute(
            "SELECT category, subcategory FROM annotations WHERE "
            "(category IS NOT NULL AND category_id IS NULL) "
            "OR (subcategory IS NOT NULL AND subcategory_id IS NULL)"
        ).fetchall()
        assert [tuple(r) for r in missing] == []

    def test_the_ids_point_at_the_rows_the_names_mean(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        row = conn.execute(
            "SELECT c.name AS cat, s.name AS sub, s.parent_id, a.category_id "
            "FROM annotations a JOIN categories c ON c.id = a.category_id "
            "JOIN categories s ON s.id = a.subcategory_id WHERE a.category = 'Food & Dining' "
            "AND a.subcategory = 'Food Delivery' LIMIT 1"
        ).fetchone()
        assert (row["cat"], row["sub"]) == ("Food & Dining", "Food Delivery")
        assert row["parent_id"] == row["category_id"]


class TestBackfillMigration:
    MIGRATION = "035_backfill_annotation_category_ids.sql"

    def _run(self, conn):
        conn.executescript((MIGRATIONS / self.MIGRATION).read_text())

    def _names(self, conn):
        return conn.execute(
            "SELECT id, category, subcategory FROM annotations ORDER BY id").fetchall()

    def _strip_ids(self, conn):
        conn.execute("UPDATE annotations SET category_id = NULL, subcategory_id = NULL")
        conn.commit()

    def test_a_ledger_seeded_by_0_1_2_ends_with_no_orphans_and_the_same_names(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        before = [tuple(r) for r in self._names(conn)]
        self._strip_ids(conn)
        assert client.get("/api/categories/tidy-up").json()["orphans"]
        self._run(conn)
        assert client.get("/api/categories/tidy-up").json()["orphans"] == []
        assert [tuple(r) for r in self._names(conn)] == before

    def test_an_unknown_subcategory_keeps_its_null(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        conn.execute(
            "UPDATE annotations SET subcategory = 'Not A Real Subcategory' WHERE id = "
            "(SELECT id FROM annotations WHERE subcategory = 'Food Delivery' LIMIT 1)")
        self._strip_ids(conn)
        self._run(conn)
        row = conn.execute(
            "SELECT category_id, subcategory, subcategory_id FROM annotations "
            "WHERE subcategory = 'Not A Real Subcategory'").fetchone()
        assert row["category_id"] == "cat_food_dining"
        assert row["subcategory_id"] is None

    def test_an_ambiguous_name_is_left_alone(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        conn.execute(
            "INSERT INTO categories (id, name, parent_id) VALUES ('dupe', 'Food Delivery', 'cat_food_dining')")
        self._strip_ids(conn)
        self._run(conn)
        assert conn.execute(
            "SELECT COUNT(*) FROM annotations WHERE subcategory = 'Food Delivery' "
            "AND subcategory_id IS NOT NULL").fetchone()[0] == 0
        # The category itself was unambiguous, so it is filled.
        assert conn.execute(
            "SELECT COUNT(*) FROM annotations WHERE subcategory = 'Food Delivery' "
            "AND category_id IS NULL").fetchone()[0] == 0

    def test_it_never_overwrites_an_id_that_is_already_set(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        conn.execute("INSERT INTO categories (id, name, parent_id) VALUES ('mine', 'Mine', 'cat_food_dining')")
        conn.execute("UPDATE annotations SET subcategory_id = 'mine' WHERE subcategory = 'Food Delivery'")
        conn.commit()
        self._run(conn)
        assert conn.execute(
            "SELECT COUNT(*) FROM annotations WHERE subcategory = 'Food Delivery' "
            "AND subcategory_id != 'mine'").fetchone()[0] == 0

    def test_it_is_idempotent_and_a_no_op_on_a_ledger_with_nothing_to_fill(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        snapshot = conn.execute(
            "SELECT id, category_id, subcategory_id FROM annotations ORDER BY id").fetchall()
        self._run(conn)
        self._run(conn)
        assert conn.execute(
            "SELECT id, category_id, subcategory_id FROM annotations ORDER BY id").fetchall() == snapshot


def _add_real_statement(conn):
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month) "
        "VALUES ('real1', 'kotak', '1', '2026-05')")
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
        "VALUES ('real_t1', 'real1', '2026-05-02', 99, 'debit', 'UPI/CHAI')")
    conn.execute(
        "INSERT INTO annotations (id, transaction_id, category, confidence, source) "
        "VALUES ('real_a1', 'real_t1', 'Food & Dining', 1.0, 'manual')")
    conn.execute("INSERT INTO people (id, name) VALUES ('real_p1', 'Real Person')")
    conn.commit()


class TestDemoStatus:
    def test_empty_ledger_has_no_sample_data(self, client_conn):
        client, _ = client_conn
        assert client.get("/api/data/demo").json() == {"loaded": False, "mixed": False}

    def test_loaded_after_seeding(self, client_conn):
        client, _ = client_conn
        client.post("/api/data/demo")
        assert client.get("/api/data/demo").json() == {"loaded": True, "mixed": False}

    def test_real_statements_alone_are_not_sample_data(self, client_conn):
        client, conn = client_conn
        _add_real_statement(conn)
        assert client.get("/api/data/demo").json() == {"loaded": False, "mixed": False}

    def test_a_ledger_holding_both_reports_mixed(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        _add_real_statement(conn)
        assert client.get("/api/data/demo").json() == {"loaded": True, "mixed": True}


class TestRemoveDemoData:
    def _count(self, conn, table, where="1=1"):
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]

    def test_removes_everything_the_seeder_wrote(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        # Derived rows a demo user produces by using the app on the sample ledger.
        t = conn.execute("SELECT id FROM transactions LIMIT 1").fetchone()[0]
        conn.execute("INSERT INTO embedding_meta (id, transaction_id, model_version) VALUES ('e1', ?, 'm')", (t,))
        conn.execute(
            "INSERT INTO annotation_jobs (id, status, statement_id, kind) "
            "VALUES ('j1', 'completed', 'demo_stmt_x', 'annotate')")
        conn.execute(
            "INSERT INTO annotation_jobs (id, status, statement_id, kind) "
            "VALUES ('j2', 'completed', (SELECT id FROM statements LIMIT 1), 'annotate')")
        conn.commit()
        r = client.delete("/api/data/demo")
        assert r.status_code == 200
        assert r.json()["transactions"] > 100
        for table in ("statements", "transactions", "annotations", "people", "transaction_groups",
                      "transaction_group_members", "transaction_links", "user_rules",
                      "embedding_meta", "annotation_jobs"):
            assert self._count(conn, table) == 0, table
        assert client.get("/api/data/demo").json() == {"loaded": False, "mixed": False}

    def test_a_real_row_survives(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        _add_real_statement(conn)
        client.delete("/api/data/demo")
        assert self._count(conn, "statements") == 1
        assert self._count(conn, "transactions", "id = 'real_t1'") == 1
        assert self._count(conn, "annotations", "id = 'real_a1'") == 1
        assert self._count(conn, "people", "id = 'real_p1'") == 1
        assert self._count(conn, "people") == 1

    def test_a_real_group_keeps_existing_but_loses_demo_members(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        _add_real_statement(conn)
        demo_txn = conn.execute("SELECT id FROM transactions WHERE id LIKE 'demo_%' LIMIT 1").fetchone()[0]
        conn.execute("INSERT INTO transaction_groups (id, name) VALUES ('g_real', 'Mine')")
        conn.execute("INSERT INTO transaction_group_members (group_id, transaction_id, txn_type) "
                     "VALUES ('g_real', ?, 'event')", (demo_txn,))
        conn.execute("INSERT INTO transaction_group_members (group_id, transaction_id, txn_type) "
                     "VALUES ('g_real', 'real_t1', 'event')")
        conn.commit()
        assert client.delete("/api/data/demo").status_code == 200
        assert [r[0] for r in conn.execute(
            "SELECT transaction_id FROM transaction_group_members WHERE group_id = 'g_real'")] == ["real_t1"]

    def test_the_category_taxonomy_is_untouched(self, client_conn):
        client, conn = client_conn
        before = self._count(conn, "categories")
        client.post("/api/data/demo")
        client.delete("/api/data/demo")
        assert self._count(conn, "categories") == before

    def test_with_nothing_to_remove_it_is_a_no_op(self, client_conn):
        client, _ = client_conn
        r = client.delete("/api/data/demo")
        assert r.status_code == 200
        assert r.json()["transactions"] == 0

    def test_refused_while_a_categorization_job_is_running(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        conn.execute(
            "INSERT INTO annotation_jobs (id, status, kind) VALUES ('j1', 'running', 'annotate')")
        conn.commit()
        r = client.delete("/api/data/demo")
        assert r.status_code == 409
        assert self._count(conn, "transactions") > 0

    def test_the_demo_can_be_loaded_again_afterwards(self, client_conn):
        client, conn = client_conn
        client.post("/api/data/demo")
        client.delete("/api/data/demo")
        assert client.post("/api/data/demo").status_code == 200
        assert self._count(conn, "transactions") > 100


class TestUploadWhileSampleDataPresent:
    def test_refused_with_a_message_that_says_what_to_do(self, client_conn):
        client, _ = client_conn
        client.post("/api/data/demo")
        r = client.post("/api/statements/upload", files={"file": ("s.pdf", b"%PDF-1.4", "application/pdf")})
        assert r.status_code == 409
        assert "sample data" in r.json()["detail"].lower()
        assert "remove" in r.json()["detail"].lower()
