"""A note on a payment: the user's own, replacing the UPI note wherever a row shows one.

Like a payment or payee name (migration 037) a note is a label. The printed
line, the stored UPI meta and everything the pipeline reads stay as they are.
"""
from __future__ import annotations

import ast
import csv
import io
import json
import sqlite3
from unittest.mock import patch

import pytest
import ulid
from fastapi.testclient import TestClient
from source_tree import NOT_A_CHECKOUT, source_root

from finance_copilot.db.connection import MIGRATIONS_DIR, init_db
from finance_copilot.db.queries.annotations import insert_annotation
from finance_copilot.db.queries.learned_rules import lookup_learned_rule, verified_labels
from finance_copilot.db.queries.payee import MAX_NOTE_LENGTH
from finance_copilot.db.queries.people import matched_transaction_ids, person_token_matches
from finance_copilot.db.queries.user_rules import (
    _matching_rows,
    create_user_rule,
    list_user_rules,
    match_user_rule,
)
from finance_copilot.models.annotation import Annotation
from finance_copilot.parsers.description import parse_description
from finance_copilot.pipeline.counterparty import normalize_identity
from finance_copilot.pipeline.embed import build_embed_text
from finance_copilot.pipeline.llm import _build_user_prompt
from finance_copilot.pipeline.rules import match_view

CHAI = "UPI/CHAI WALA CORNER/501571919522/PAYTM QR/chai and samosa"
NEFT = "NEFT/ACME TRADERS/REF123"
ATM = "ATM WITHDRAWAL HDFC INDIRANAGAR"


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s1','test','1','2026-01')"
    )
    yield conn
    conn.close()


@pytest.fixture
def client(conn):
    from finance_copilot.api.deps import get_db
    from finance_copilot.main import app

    app.dependency_overrides[get_db] = lambda: conn
    with patch("finance_copilot.api.routes.annotations._embed_in_background"):
        yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def add_txn(conn, raw, *, date="2026-01-15", amount=100.0, dc="debit", annotate=True):
    txn_id = str(ulid.ULID())
    parsed = parse_description(raw)
    meta = None
    if parsed.is_upi:
        meta = json.dumps({"vpa": parsed.vpa, "ref": parsed.ref, "note": parsed.note, "shape": parsed.shape})
    conn.execute(
        """INSERT INTO transactions
           (id, statement_id, txn_date, amount, debit_credit, raw_description, upi_meta, counterparty_key)
           VALUES (?, 's1', ?, ?, ?, ?, ?, ?)""",
        (txn_id, date, amount, dc, raw, meta, normalize_identity(raw)),
    )
    if annotate:
        insert_annotation(conn, Annotation(
            id=str(ulid.ULID()), transaction_id=txn_id, category="Shopping", merchant=None,
            tags="", confidence=0.9, source="manual",
        ))
    conn.commit()
    return txn_id


def put_note(client, txn_id, note):
    return client.put(f"/api/transactions/{txn_id}/note", json={"note": note})


def get_txn(client, txn_id):
    return client.get(f"/api/transactions/{txn_id}").json()


def listed(client, query=""):
    return {r["id"]: r for r in client.get(f"/api/transactions?include=annotation{query}").json()}


class TestMigration:
    def test_adds_the_nullable_column(self, conn):
        cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(transactions)")}
        assert "user_note" in cols
        assert cols["user_note"]["notnull"] == 0

    def test_file_is_numbered_042(self):
        assert [p.name for p in MIGRATIONS_DIR.glob("042_*.sql")] == ["042_payment_notes.sql"]


class TestSetAndClear:
    def test_sets_a_note_and_returns_the_updated_row(self, conn, client):
        txn = add_txn(conn, ATM)
        res = put_note(client, txn, "  cash for the plumber  ")
        assert res.status_code == 200
        row = res.json()
        assert row["id"] == txn
        assert row["note"] == "cash for the plumber"
        assert row["user_note"] == "cash for the plumber"
        assert row["note_is_yours"] is True
        # The same shape the rename route returns.
        assert {"payee", "payee_all", "channel", "raw_description", "category"} <= set(row)
        assert get_txn(client, txn)["note"] == "cash for the plumber"

    def test_a_payment_with_no_upi_note_has_none_until_one_is_written(self, conn, client):
        txn = add_txn(conn, NEFT)
        before = get_txn(client, txn)
        assert before["note"] is None
        assert before["note_is_yours"] is False

    def test_replaces_the_upi_note(self, conn, client):
        txn = add_txn(conn, CHAI)
        assert get_txn(client, txn)["note"] == "chai and samosa"
        assert get_txn(client, txn)["note_is_yours"] is False
        put_note(client, txn, "team tea")
        assert get_txn(client, txn)["note"] == "team tea"
        assert get_txn(client, txn)["note_is_yours"] is True

    def test_replaces_its_own_earlier_note(self, conn, client):
        txn = add_txn(conn, CHAI)
        put_note(client, txn, "first")
        put_note(client, txn, "second")
        assert get_txn(client, txn)["note"] == "second"

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_clearing_brings_the_upi_note_back(self, conn, client, empty):
        txn = add_txn(conn, CHAI)
        put_note(client, txn, "team tea")
        res = put_note(client, txn, empty)
        assert res.status_code == 200
        row = res.json()
        assert row["user_note"] is None
        assert row["note"] == "chai and samosa"
        assert row["note_is_yours"] is False

    def test_clearing_a_note_on_a_payment_with_no_upi_note_leaves_none(self, conn, client):
        txn = add_txn(conn, NEFT)
        put_note(client, txn, "invoice 12")
        assert put_note(client, txn, "").json()["note"] is None

    def test_unknown_payment_is_404(self, client):
        assert put_note(client, "nope", "x").status_code == 404

    def test_the_length_cap(self, conn, client):
        txn = add_txn(conn, CHAI)
        assert put_note(client, txn, "x" * MAX_NOTE_LENGTH).status_code == 200
        assert put_note(client, txn, "x" * (MAX_NOTE_LENGTH + 1)).status_code == 422
        assert MAX_NOTE_LENGTH == 500

    def test_a_note_belongs_to_that_payment_only(self, conn, client):
        a = add_txn(conn, CHAI, date="2026-01-01")
        b = add_txn(conn, CHAI.replace("501571919522", "509999999999"), date="2026-02-01")
        put_note(client, a, "team tea")
        rows = listed(client)
        assert rows[a]["note"] == "team tea"
        assert rows[b]["note"] == "chai and samosa"

    def test_leaves_the_printed_line_the_upi_meta_and_the_labels_alone(self, conn, client):
        txn = add_txn(conn, CHAI)
        before = dict(conn.execute("SELECT * FROM transactions WHERE id = ?", (txn,)).fetchone())
        before_row = get_txn(client, txn)
        put_note(client, txn, "team tea")
        after = dict(conn.execute("SELECT * FROM transactions WHERE id = ?", (txn,)).fetchone())
        assert {k: v for k, v in after.items() if k != "user_note"} == {
            k: v for k, v in before.items() if k != "user_note"
        }
        after_row = get_txn(client, txn)
        for field in ("raw_description", "counterparty_key", "category", "merchant", "source", "payee", "upi_meta"):
            assert after_row[field] == before_row[field]

    def test_works_for_a_credit_and_for_a_payment_with_no_payee_key(self, conn, client):
        txn = add_txn(conn, "NEFT/SAL CREDIT/TECHWAVE SOLUTIONS PVT LTD", dc="credit", annotate=False)
        assert put_note(client, txn, "March pay").json()["note"] == "March pay"


class TestEveryApiResolvesTheNote:
    def test_the_list_and_the_plain_list(self, conn, client):
        txn = add_txn(conn, CHAI)
        put_note(client, txn, "team tea")
        assert listed(client)[txn]["note"] == "team tea"
        assert {r["id"]: r for r in client.get("/api/transactions").json()}[txn]["note"] == "team tea"

    def test_the_review_queue(self, conn, client):
        txn = add_txn(conn, CHAI, annotate=False)
        put_note(client, txn, "team tea")
        items = client.get("/api/annotations/review-queue").json()
        item = next(i for i in items if i["transaction_id"] == txn)
        assert item["note"] == "team tea"
        assert item["note_is_yours"] is True

    def test_group_members(self, conn, client):
        txn = add_txn(conn, CHAI)
        put_note(client, txn, "team tea")
        group = client.post("/api/groups", json={"name": "Office"}).json()
        client.post(f"/api/groups/{group['id']}/members", json={"transaction_id": txn})
        members = client.get(f"/api/groups/{group['id']}").json()["members"]
        assert [m["note"] for m in members] == ["team tea"]

    def test_similar_payments(self, conn, client):
        a = add_txn(conn, CHAI, date="2026-01-01")
        b = add_txn(conn, CHAI.replace("501571919522", "509999999999"), date="2026-02-01")
        conn.execute("UPDATE annotations SET source = 'llm' WHERE transaction_id = ?", (b,))
        conn.commit()
        put_note(client, b, "team tea")
        ann = conn.execute("SELECT id FROM annotations WHERE transaction_id = ?", (a,)).fetchone()["id"]
        items = client.get(f"/api/annotations/{ann}/similar").json()
        assert [i["note"] for i in items] == ["team tea"]

    def test_export_carries_the_resolved_note(self, conn, client):
        mine = add_txn(conn, NEFT)
        upi = add_txn(conn, CHAI)
        none = add_txn(conn, ATM)
        put_note(client, mine, "invoice 12")
        rows = {r["id"]: r for r in client.get("/api/transactions/export?format=json").json()}
        assert rows[mine]["note"] == "invoice 12"
        assert rows[upi]["note"] == "chai and samosa"
        assert rows[none]["note"] is None
        csv_text = client.get("/api/transactions/export?format=csv").text
        header = csv_text.splitlines()[0].split(",")
        assert "note" in header
        assert "invoice 12" in csv_text


class TestCsvExportIsSafe:
    """A spreadsheet runs a cell that starts with = + - @ (or tab / CR) as a formula."""

    def _rows(self, client):
        text = client.get("/api/transactions/export?format=csv").text
        return list(csv.DictReader(io.StringIO(text)))

    def test_text_cells_that_start_like_a_formula_are_defused(self, conn, client):
        txn = add_txn(conn, "=HYPERLINK(\"http://evil\",\"x\")", amount=42.5)
        put_note(client, txn, "@SUM(A1:A9)")
        client.put(f"/api/transactions/{txn}/name", json={"name": "+cmd|' /C calc'!A0"})
        conn.execute(
            "UPDATE annotations SET merchant = '-2+3', subcategory = '\tTab', tags = '[\"=x\"]' WHERE transaction_id = ?",
            (txn,),
        )
        conn.execute("UPDATE annotations SET category = '=Cat' WHERE transaction_id = ?", (txn,))
        conn.commit()

        (row,) = self._rows(client)
        assert row["raw_description"] == "'=HYPERLINK(\"http://evil\",\"x\")"
        assert row["note"] == "'@SUM(A1:A9)"
        assert row["payee"] == "'+cmd|' /C calc'!A0"
        assert row["merchant"] == "'-2+3"
        assert row["subcategory"] == "'\tTab"
        assert row["category"] == "'=Cat"
        assert row["tags"] == "'=x"

    def test_carriage_return_start_is_defused_too(self, conn, client):
        txn = add_txn(conn, CHAI)
        conn.execute("UPDATE transactions SET raw_description = ? WHERE id = ?", ("\rSneaky", txn))
        conn.commit()
        (row,) = self._rows(client)
        assert row["raw_description"] == "'\rSneaky"

    def test_numbers_stay_numbers_and_ordinary_text_is_untouched(self, conn, client):
        txn = add_txn(conn, CHAI, amount=250.5)
        conn.execute("UPDATE transactions SET running_balance = -1200.75 WHERE id = ?", (txn,))
        conn.commit()
        (row,) = self._rows(client)
        assert row["amount"] == "250.5"
        assert row["running_balance"] == "-1200.75"
        assert row["debit_credit"] == "debit"
        assert row["note"] == "chai and samosa"
        assert not row["payee"].startswith("'")

    def test_json_export_is_unchanged(self, conn, client):
        txn = add_txn(conn, NEFT)
        put_note(client, txn, "=1+1")
        (row,) = client.get("/api/transactions/export?format=json").json()
        assert row["note"] == "=1+1"


class TestSearch:
    def test_finds_a_payment_by_the_note_the_user_wrote(self, conn, client):
        a = add_txn(conn, NEFT)
        add_txn(conn, ATM)
        put_note(client, a, "Plumber advance")
        assert set(listed(client, "&q=plumber")) == {a}
        assert client.get("/api/transactions/summary?q=plumber").json()["count"] == 1

    def test_finds_a_payment_by_its_upi_note(self, conn, client):
        a = add_txn(conn, CHAI)
        add_txn(conn, ATM)
        assert set(listed(client, "&q=samosa")) == {a}

    def test_still_finds_it_by_the_upi_note_after_the_user_replaced_it(self, conn, client):
        a = add_txn(conn, CHAI)
        put_note(client, a, "team tea")
        assert set(listed(client, "&q=samosa")) == {a}
        assert set(listed(client, "&q=team")) == {a}

    def test_a_cleared_note_no_longer_matches(self, conn, client):
        a = add_txn(conn, NEFT)
        put_note(client, a, "Plumber advance")
        put_note(client, a, "")
        assert listed(client, "&q=plumber") == {}

    def test_wildcards_in_the_query_match_literally(self, conn, client):
        a = add_txn(conn, NEFT)
        b = add_txn(conn, ATM)
        put_note(client, a, "100% done")
        put_note(client, b, "1000 done")
        assert set(listed(client, "&q=100%25")) == {a}
        c = add_txn(conn, CHAI)
        put_note(client, c, "a_b")
        put_note(client, b, "axb")
        assert set(listed(client, "&q=a_b")) == {c}

    def test_the_palette_search_is_the_same_filter(self, conn, client):
        """The palette calls GET /transactions?q=...&include=annotation&limit=N."""
        a = add_txn(conn, NEFT)
        put_note(client, a, "Plumber advance")
        res = client.get("/api/transactions?q=plumber&include=annotation&limit=8").json()
        assert [r["id"] for r in res] == [a]
        assert res[0]["note"] == "Plumber advance"


def _reaches_payee_labels(source: str) -> list[str]:
    """Imports of db.queries.payee, and any use of with_payee, in a module's code
    (docstrings and comments that merely mention them do not count)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.endswith("queries.payee"):
                found.append(f"imports {module}")
            if module.endswith("db.queries") or module == "queries":
                found += [f"imports {module}.{a.name}" for a in node.names if a.name == "payee"]
            found += [f"imports {a.name}" for a in node.names if a.name == "with_payee"]
        elif isinstance(node, ast.Import):
            found += [f"imports {a.name}" for a in node.names if a.name.endswith("queries.payee")]
        elif isinstance(node, ast.Name) and node.id == "with_payee":
            found.append("uses with_payee")
        elif isinstance(node, ast.Attribute) and node.attr == "with_payee":
            found.append("uses with_payee")
    return found


class TestTheGuardCatchesWhatItShould:
    @pytest.mark.parametrize("source", [
        "from finance_copilot.db.queries.payee import with_payee",
        "from finance_copilot.db.queries import payee",
        "import finance_copilot.db.queries.payee",
        "from finance_copilot.db.queries.payee import load_payee_names",
        "rows = payee.with_payee(conn, rows)",
    ])
    def test_flags_a_reach_for_the_labels(self, source):
        assert _reaches_payee_labels(source)

    def test_ignores_docstrings_and_the_parsers_payee_module(self):
        source = (
            '"""added by db.queries.payee.with_payee"""\n'
            "from finance_copilot.parsers.payee import classify_payee\n"
        )
        assert _reaches_payee_labels(source) == []


class TestThePipelineNeverReadsTheNote:
    """A note is a label: nothing that matches, sorts or embeds may change when one is set."""

    def _row(self, conn, txn):
        return dict(conn.execute("SELECT * FROM transactions WHERE id = ?", (txn,)).fetchone())

    @pytest.mark.parametrize("raw", [CHAI, NEFT, ATM])
    def test_matching_embedding_and_the_prompt_do_not_move(self, conn, client, raw):
        txn = add_txn(conn, raw, annotate=False)
        before = self._row(conn, txn)
        embed_before = build_embed_text(before)
        view_before = match_view(before)
        prompt_before = _build_user_prompt(before, ["Shopping", "Food"])

        put_note(client, txn, "zebra crossing salary")
        after = self._row(conn, txn)

        assert after["counterparty_key"] == before["counterparty_key"]
        assert build_embed_text(after) == embed_before
        assert match_view(after) == view_before
        prompt_after = _build_user_prompt(after, ["Shopping", "Food"])
        assert prompt_after == prompt_before
        assert "zebra" not in prompt_after
        assert "zebra" not in embed_before + build_embed_text(after)

    def test_setting_a_note_changes_no_annotation(self, conn, client):
        txn = add_txn(conn, CHAI)
        before = dict(conn.execute("SELECT * FROM annotations WHERE transaction_id = ?", (txn,)).fetchone())
        put_note(client, txn, "team tea")
        after = dict(conn.execute("SELECT * FROM annotations WHERE transaction_id = ?", (txn,)).fetchone())
        assert after == before

    def test_people_and_rule_matching_return_the_same_after_a_note(self, conn, client):
        txn = add_txn(conn, CHAI)
        other = add_txn(conn, NEFT)
        create_user_rule(conn, "chai", "Shopping")
        create_user_rule(conn, "zebra", "Shopping")
        conn.commit()
        key = self._row(conn, txn)["counterparty_key"]

        def snapshot():
            row = self._row(conn, txn)
            rules = list_user_rules(conn)
            return {
                "people_by_name": matched_transaction_ids(conn, "chai"),
                "people_by_note_word": matched_transaction_ids(conn, "zebra"),
                "token_match": person_token_matches("chai", None, row["counterparty_key"], row["raw_description"].lower()),
                "token_note_word": person_token_matches("zebra", None, row["counterparty_key"], row["raw_description"].lower()),
                "user_rule": (match_user_rule(rules, row) or {}).get("pattern"),
                "other_user_rule": match_user_rule(rules, self._row(conn, other)),
                "preview": sorted(r["id"] for r in _matching_rows(conn, "zebra")),
                "preview_chai": sorted(r["id"] for r in _matching_rows(conn, "chai")),
                "labels": verified_labels(conn, key, "debit", before_txn_date=None),
                "learned": lookup_learned_rule(conn, key, "debit", before_txn_date=None),
            }

        before = snapshot()
        assert before["people_by_name"] == [txn]
        assert before["user_rule"] == "chai"
        put_note(client, txn, "zebra crossing salary")
        put_note(client, other, "chai zebra")
        assert snapshot() == before
        assert before["people_by_note_word"] == [] and before["preview"] == []

    @pytest.mark.parametrize("folder", ["pipeline", "parsers"])
    def test_no_pipeline_or_parser_code_reads_the_labels_layer(self, folder):
        """Notes and names are labels. Neither the pipeline nor the parsers may import
        db.queries.payee or call with_payee, which is where labels are resolved."""
        root = source_root()
        if root is None:
            pytest.skip(NOT_A_CHECKOUT)
        offenders = []
        for path in sorted((root / "finance_copilot" / folder).rglob("*.py")):
            offenders += [f"{path.relative_to(root)}: {why}" for why in _reaches_payee_labels(path.read_text())]
        assert offenders == [], f"labels are for display; these reach for them: {offenders}"

    @pytest.mark.parametrize("name", ["people", "learned_rules", "user_rules"])
    def test_the_matching_queries_never_touch_the_note(self, name):
        root = source_root()
        if root is None:
            pytest.skip(NOT_A_CHECKOUT)
        text = (root / "finance_copilot" / "db" / "queries" / f"{name}.py").read_text()
        assert "user_note" not in text
        assert _reaches_payee_labels(text) == []

    def test_no_pipeline_or_parser_source_mentions_the_user_note(self):
        root = source_root()
        if root is None:
            pytest.skip(NOT_A_CHECKOUT)
        offenders = []
        for folder in ("pipeline", "parsers"):
            for path in sorted((root / "finance_copilot" / folder).rglob("*.py")):
                if "user_note" in path.read_text():
                    offenders.append(str(path.relative_to(root)))
        assert offenders == [], f"the user's note is a label; these read it: {offenders}"
