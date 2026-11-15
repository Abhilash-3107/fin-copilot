"""Payee names: one resolution (payment name > payee name > annotation merchant >
parsed counterparty > raw line), the two ways to rename, and every API that
returns the resolved name."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
import ulid
from fastapi.testclient import TestClient

from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.annotations import insert_annotation
from finance_copilot.db.queries.insights import summarize_insights
from finance_copilot.models.annotation import Annotation
from finance_copilot.parsers.description import channel_of, parse_description
from finance_copilot.pipeline.counterparty import normalize_identity

AMIT = "UPI/AMIT.LANDLORD@OKSBI/501234567890/rent"
CHAI = "UPI/CHAI WALA CORNER/501571919522/PAYTM QR/chai and samosa"
SALARY = "NEFT/SAL CREDIT/TECHWAVE SOLUTIONS PVT LTD"


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


def add_txn(conn, raw, *, date="2026-01-15", amount=100.0, dc="debit", merchant=None,
            category="Shopping", note=None, annotate=True):
    txn_id = str(ulid.ULID())
    parsed = parse_description(raw)
    meta = None
    if parsed.is_upi:
        import json

        meta = json.dumps({"vpa": parsed.vpa, "ref": parsed.ref, "note": note or parsed.note, "shape": parsed.shape})
    conn.execute(
        """INSERT INTO transactions
           (id, statement_id, txn_date, amount, debit_credit, raw_description, upi_meta, counterparty_key)
           VALUES (?, 's1', ?, ?, ?, ?, ?, ?)""",
        (txn_id, date, amount, dc, raw, meta, normalize_identity(raw)),
    )
    if annotate:
        insert_annotation(conn, Annotation(
            id=str(ulid.ULID()), transaction_id=txn_id, category=category, merchant=merchant,
            tags="", confidence=0.9, source="manual",
        ))
    conn.commit()
    return txn_id


def get_txn(client, txn_id):
    return client.get(f"/api/transactions/{txn_id}").json()


def listed(client, query=""):
    return {r["id"]: r for r in client.get(f"/api/transactions?include=annotation{query}").json()}


class TestMigration:
    def test_creates_the_payee_table_and_the_payment_column(self, conn):
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(payee_names)")}
        assert {"counterparty_key", "name", "updated_at"} <= cols
        assert "display_name" in {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}

    def test_file_is_numbered_037(self):
        names = [p.name for p in (Path(__file__).parents[1] / "db" / "migrations").glob("037_*.sql")]
        assert names == ["037_payee_names.sql"]


class TestResolutionOrder:
    def test_raw_line_when_nothing_parses(self, conn, client):
        txn = add_txn(conn, "SOME BANK LINE 123", annotate=False)
        assert get_txn(client, txn)["payee"] == "SOME BANK LINE 123"

    def test_unrecognised_upi_shape_falls_back_to_the_raw_line(self, conn, client):
        raw = "UPI/SOMEONE/NOREFHERE"
        txn = add_txn(conn, raw, annotate=False)
        assert get_txn(client, txn)["payee"] == raw

    def test_parsed_counterparty_is_tidied(self, conn, client):
        chai = add_txn(conn, CHAI, annotate=False)
        vpa = add_txn(conn, AMIT, annotate=False)
        assert get_txn(client, chai)["payee"] == "Chai Wala Corner"
        assert get_txn(client, vpa)["payee"] == "amit.landlord@oksbi"

    def test_annotation_merchant_beats_the_parsed_name(self, conn, client):
        txn = add_txn(conn, AMIT, merchant="Amit Kumar")
        assert get_txn(client, txn)["payee"] == "Amit Kumar"

    def test_blank_merchant_is_ignored(self, conn, client):
        txn = add_txn(conn, CHAI, merchant="  ")
        assert get_txn(client, txn)["payee"] == "Chai Wala Corner"

    def test_payee_name_beats_the_merchant(self, conn, client):
        txn = add_txn(conn, AMIT, merchant="Amit Kumar")
        assert client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Rent"}).status_code == 200
        assert get_txn(client, txn)["payee"] == "Rent"

    def test_payment_name_beats_the_payee_name(self, conn, client):
        txn = add_txn(conn, AMIT, merchant="Amit Kumar")
        client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Rent"})
        assert client.put(f"/api/transactions/{txn}/name", json={"name": "March rent"}).status_code == 200
        row = get_txn(client, txn)
        assert row["payee"] == "March rent"
        assert row["display_name"] == "March rent"
        assert row["payee_name"] == "Rent"
        # What every payment to this payee is called, without this payment's own name.
        assert row["payee_all"] == "Rent"

    def test_renaming_never_touches_the_printed_line_key_or_label(self, conn, client):
        txn = add_txn(conn, AMIT, merchant="Amit Kumar")
        before = get_txn(client, txn)
        client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Rent"})
        client.put(f"/api/transactions/{txn}/name", json={"name": "March rent"})
        after = get_txn(client, txn)
        for field in ("raw_description", "counterparty_key", "category", "merchant", "source", "tags"):
            assert after[field] == before[field]


class TestRenamePayee:
    def test_applies_to_every_payment_to_the_payee_past_and_future(self, conn, client):
        past = add_txn(conn, AMIT, date="2026-01-01")
        other = add_txn(conn, CHAI)
        client.put(f"/api/transactions/{past}/payee-name", json={"name": "Rent"})
        future = add_txn(conn, AMIT.replace("501234567890", "509999999999"), date="2026-03-01")
        rows = listed(client)
        assert rows[past]["payee"] == "Rent"
        assert rows[future]["payee"] == "Rent"
        assert rows[other]["payee"] == "Chai Wala Corner"

    def test_also_clears_the_payment_s_own_name_so_this_payment_shows_it(self, conn, client):
        txn = add_txn(conn, AMIT)
        client.put(f"/api/transactions/{txn}/name", json={"name": "Odd one"})
        client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Rent"})
        row = get_txn(client, txn)
        assert row["display_name"] is None
        assert row["payee"] == "Rent"

    def test_a_payment_with_another_own_name_keeps_it(self, conn, client):
        first = add_txn(conn, AMIT, date="2026-01-01")
        second = add_txn(conn, AMIT.replace("501234567890", "509999999999"), date="2026-02-01")
        client.put(f"/api/transactions/{second}/name", json={"name": "Deposit"})
        client.put(f"/api/transactions/{first}/payee-name", json={"name": "Rent"})
        rows = listed(client)
        assert rows[first]["payee"] == "Rent"
        assert rows[second]["payee"] == "Deposit"

    def test_clearing_falls_back_to_the_merchant(self, conn, client):
        txn = add_txn(conn, AMIT, merchant="Amit Kumar")
        client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Rent"})
        assert client.put(f"/api/transactions/{txn}/payee-name", json={"name": None}).status_code == 200
        assert get_txn(client, txn)["payee"] == "Amit Kumar"
        assert conn.execute("SELECT COUNT(*) FROM payee_names").fetchone()[0] == 0

    def test_a_blank_name_clears(self, conn, client):
        txn = add_txn(conn, AMIT)
        client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Rent"})
        client.put(f"/api/transactions/{txn}/payee-name", json={"name": "   "})
        assert get_txn(client, txn)["payee_name"] is None

    def test_a_payment_without_a_payee_cannot_have_one_renamed(self, conn, client):
        txn = add_txn(conn, SALARY, dc="credit", annotate=False)
        res = client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Pay"})
        assert res.status_code == 409

    def test_unknown_payment_is_404(self, client):
        assert client.put("/api/transactions/nope/payee-name", json={"name": "x"}).status_code == 404
        assert client.put("/api/transactions/nope/name", json={"name": "x"}).status_code == 404

    def test_a_very_long_name_is_refused(self, conn, client):
        txn = add_txn(conn, AMIT)
        assert client.put(f"/api/transactions/{txn}/payee-name", json={"name": "x" * 500}).status_code == 422

    def test_the_name_follows_the_payee_when_its_key_is_rekeyed(self, conn):
        from finance_copilot.pipeline.counterparty import rekey_counterparty

        conn.execute("INSERT INTO payee_names (counterparty_key, name) VALUES ('OBEROIFC TUCKSH', 'Tea')")
        rekey_counterparty(conn, "OBEROIFC TUCKSH", "OBEROIFC TUCKSHOP")
        assert [tuple(r) for r in conn.execute("SELECT counterparty_key, name FROM payee_names")] == [
            ("OBEROIFC TUCKSHOP", "Tea")
        ]


class TestRenamePayment:
    def test_applies_to_that_payment_only(self, conn, client):
        a = add_txn(conn, AMIT, date="2026-01-01")
        b = add_txn(conn, AMIT.replace("501234567890", "509999999999"), date="2026-02-01")
        client.put(f"/api/transactions/{a}/name", json={"name": "Deposit"})
        rows = listed(client)
        assert rows[a]["payee"] == "Deposit"
        assert rows[b]["payee"] == "amit.landlord@oksbi"

    def test_works_for_a_payment_with_no_payee_key(self, conn, client):
        txn = add_txn(conn, SALARY, dc="credit", annotate=False)
        client.put(f"/api/transactions/{txn}/name", json={"name": "Salary"})
        assert get_txn(client, txn)["payee"] == "Salary"

    def test_clearing_falls_back_to_the_payee_name(self, conn, client):
        txn = add_txn(conn, AMIT)
        client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Rent"})
        client.put(f"/api/transactions/{txn}/name", json={"name": "March rent"})
        client.put(f"/api/transactions/{txn}/name", json={"name": ""})
        row = get_txn(client, txn)
        assert row["display_name"] is None
        assert row["payee"] == "Rent"


class TestRowFields:
    def test_note_and_channel(self, conn, client):
        upi = add_txn(conn, AMIT, annotate=False)
        neft = add_txn(conn, SALARY, dc="credit", annotate=False)
        rows = listed(client)
        assert rows[upi]["note"] == "rent"
        assert rows[upi]["channel"] == "UPI"
        assert rows[neft]["note"] is None
        assert rows[neft]["channel"] == "NEFT"

    def test_the_plain_list_carries_the_payee_too(self, conn, client):
        txn = add_txn(conn, AMIT, merchant="Amit Kumar")
        rows = {r["id"]: r for r in client.get("/api/transactions").json()}
        assert rows[txn]["payee"] == "Amit Kumar"

    def test_the_printed_line_is_still_returned(self, conn, client):
        txn = add_txn(conn, AMIT, merchant="Amit Kumar")
        assert listed(client)[txn]["raw_description"] == AMIT

    @pytest.mark.parametrize("raw, channel", [
        ("UPI/X/501234567890/note", "UPI"),
        ("REV-UPI/X/501234567890/note", "UPI reversal"),
        ("NEFT/HDFC CREDIT CARD PAYMENT", "NEFT"),
        ("IMPS/P2P/TRANSFER/TO OWN SBI SAVINGS", "IMPS"),
        ("RTGS/ACME", "RTGS"),
        ("POS/THIRD WAVE COFFEE", "Card"),
        ("ATM WITHDRAWAL HDFC INDIRANAGAR", "ATM"),
        ("FLIPKART INTERNET PVT LTD", None),
        ("", None),
    ])
    def test_channel_of(self, raw, channel):
        assert channel_of(raw) == channel


class TestSearch:
    def test_matches_a_payee_name_for_every_payment_to_it(self, conn, client):
        a = add_txn(conn, AMIT, date="2026-01-01")
        b = add_txn(conn, AMIT.replace("501234567890", "509999999999"), date="2026-02-01")
        add_txn(conn, CHAI)
        client.put(f"/api/transactions/{a}/payee-name", json={"name": "Rent"})
        assert set(listed(client, "&q=rent")) == {a, b}
        assert client.get("/api/transactions/summary?q=rent").json()["count"] == 2

    def test_matches_a_payment_name(self, conn, client):
        a = add_txn(conn, AMIT, date="2026-01-01")
        add_txn(conn, AMIT.replace("501234567890", "509999999999"), date="2026-02-01")
        client.put(f"/api/transactions/{a}/name", json={"name": "Deposit"})
        assert set(listed(client, "&q=deposit")) == {a}

    def test_drill_filter_by_payee_name(self, conn, client):
        a = add_txn(conn, AMIT, date="2026-01-01")
        b = add_txn(conn, AMIT.replace("501234567890", "509999999999"), date="2026-02-01")
        add_txn(conn, CHAI)
        client.put(f"/api/transactions/{a}/payee-name", json={"name": "Rent"})
        assert set(listed(client, "&merchant=Rent")) == {a, b}


class TestInsightsKeepTheirIdentity:
    def _recurring_month(self, conn, month, amount=15000):
        return add_txn(conn, AMIT.replace("501234567890", f"50{month.replace('-', '')}111"),
                       date=f"{month}-05", amount=amount, merchant="Amit Kumar", category="Housing")

    def test_recurring_row_is_relabelled_not_split_or_merged(self, conn, client):
        ids = [self._recurring_month(conn, m) for m in ("2026-01", "2026-02", "2026-03")]
        for i, m in enumerate(("2026-01", "2026-02", "2026-03")):
            add_txn(conn, CHAI, date=f"{m}-09", amount=40 + i * 7)  # varying: not recurring
        before = summarize_insights(conn, "2026-03")["recurring"]
        client.put(f"/api/transactions/{ids[0]}/payee-name", json={"name": "Rent"})
        after = summarize_insights(conn, "2026-03")["recurring"]

        assert [r["name"] for r in before] == ["Amit Kumar"]
        assert [r["name"] for r in after] == ["Rent"]
        assert [{k: v for k, v in r.items() if k != "name"} for r in after] == \
            [{k: v for k, v in r.items() if k != "name"} for r in before]

    def test_merchant_rows_are_relabelled_with_the_same_totals(self, conn, client):
        ids = [self._recurring_month(conn, m) for m in ("2026-01", "2026-02", "2026-03")]
        add_txn(conn, CHAI, date="2026-03-09", amount=40, merchant="Chai Wala")
        before = summarize_insights(conn, "2026-03")["merchants"]
        client.put(f"/api/transactions/{ids[2]}/payee-name", json={"name": "Rent"})
        after = summarize_insights(conn, "2026-03")["merchants"]

        assert [m["name"] for m in before] == ["Amit Kumar", "Chai Wala"]
        assert [m["name"] for m in after] == ["Rent", "Chai Wala"]
        assert [(m["total"], m["count"], m["avg"]) for m in after] == \
            [(m["total"], m["count"], m["avg"]) for m in before]

    def test_one_payment_name_does_not_rename_the_row(self, conn, client):
        ids = [self._recurring_month(conn, m) for m in ("2026-01", "2026-02", "2026-03")]
        client.put(f"/api/transactions/{ids[0]}/name", json={"name": "Deposit"})
        recurring = summarize_insights(conn, "2026-03")["recurring"]
        assert [r["name"] for r in recurring] == ["Amit Kumar"]
        assert len(recurring) == 1


class TestOtherApis:
    def test_review_queue_items_carry_the_payee(self, conn, client):
        txn = add_txn(conn, AMIT, merchant="Amit Kumar", annotate=False)
        client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Rent"})
        items = client.get("/api/annotations/review-queue").json()
        assert [i["payee"] for i in items if i["transaction_id"] == txn] == ["Rent"]

    def test_group_members_carry_the_payee(self, conn, client):
        txn = add_txn(conn, AMIT, merchant="Amit Kumar")
        client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Rent"})
        group = client.post("/api/groups", json={"name": "Flat"}).json()
        client.post(f"/api/groups/{group['id']}/members", json={"transaction_id": txn})
        members = client.get(f"/api/groups/{group['id']}").json()["members"]
        assert [m["payee"] for m in members] == ["Rent"]

    def test_similar_payments_carry_the_payee(self, conn, client):
        a = add_txn(conn, AMIT, date="2026-01-01", merchant="Amit Kumar")
        add_txn(conn, AMIT.replace("501234567890", "509999999999"), date="2026-02-01")
        conn.execute("UPDATE annotations SET source = 'llm' WHERE transaction_id != ?", (a,))
        conn.commit()
        client.put(f"/api/transactions/{a}/payee-name", json={"name": "Rent"})
        ann = conn.execute("SELECT id FROM annotations WHERE transaction_id = ?", (a,)).fetchone()["id"]
        res = client.get(f"/api/annotations/{ann}/similar")
        assert res.status_code == 200
        assert res.json() and all(item["payee"] == "Rent" for item in res.json())

    def test_export_carries_the_payee(self, conn, client):
        txn = add_txn(conn, AMIT)
        client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Rent"})
        rows = client.get("/api/transactions/export?format=json").json()
        assert [r["payee"] for r in rows] == ["Rent"]


class TestWipe:
    def test_delete_everything_clears_the_names(self, conn, client):
        txn = add_txn(conn, AMIT)
        client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Rent"})
        from finance_copilot.api.routes import data

        assert "payee_names" in data._WIPE_ORDER
