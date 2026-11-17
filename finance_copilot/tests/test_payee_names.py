"""Payee names: one resolution (payment name > payee name > annotation merchant >
parsed counterparty > raw line), the two ways to rename, and every API that
returns the resolved name."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
import ulid
from fastapi.testclient import TestClient

from finance_copilot.db.connection import MIGRATIONS_DIR, init_db
from finance_copilot.db.queries.annotations import insert_annotation
from finance_copilot.db.queries.insights import summarize_insights
from finance_copilot.db.queries.payee import clean_note
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
        names = [p.name for p in MIGRATIONS_DIR.glob("037_*.sql")]
        assert names == ["037_payee_names.sql"]


class TestResolutionOrder:
    def test_raw_line_when_nothing_parses(self, conn, client):
        txn = add_txn(conn, "SOME BANK LINE 123", annotate=False)
        assert get_txn(client, txn)["payee"] == "SOME BANK LINE 123"

    def test_unrecognised_upi_shape_falls_back_to_the_line_without_its_channel(self, conn, client):
        txn = add_txn(conn, "UPI/SOMEONE/NOREFHERE", annotate=False)
        row = get_txn(client, txn)
        assert row["payee"] == "SOMEONE"
        assert row["channel"] == "UPI"

    @pytest.mark.parametrize("raw, key", [
        ("UPI/CHAI WALA CORNER/PAYTM QR", "CHAI WALA CORNER"),
        ("UPI/BLUE TOKAI COFFEE/ORDER", "BLUE TOKAI COFFEE"),
        ("UPI/LICIOUS/ORDER", "LICIOUS"),
        ("UPI/BMRCL METRO/RECHARGE", "BMRCL METRO"),
        ("UPI/APOLLO PHARMACY/BILL", "APOLLO PHARMACY"),
        ("UPI/DUNZO/DELIVERY", "DUNZO"),
    ])
    def test_an_unreferenced_upi_line_is_labelled_by_the_payee_its_key_names(self, conn, client, raw, key):
        """Label and key come from the same parse, so they never disagree: the
        line has no reference, but its payee is still the payee."""
        txn = add_txn(conn, raw, annotate=False)
        stored = conn.execute("SELECT counterparty_key FROM transactions WHERE id = ?", (txn,)).fetchone()[0]
        assert stored == key
        assert get_txn(client, txn)["payee"].upper() == stored

    @pytest.mark.parametrize("raw, fallback, channel", [
        ("POS/GLENS BAKEHOUSE 123/4567", "GLENS BAKEHOUSE 123/4567", "Card"),
        ("NEFT/HDFC CREDIT CARD PAYMENT", "HDFC CREDIT CARD PAYMENT", "NEFT"),
        ("IMPS/P2P/TRANSFER/TO OWN SBI SAVINGS", "P2P/TRANSFER/TO OWN SBI SAVINGS", "IMPS"),
        # No separator after the first word: the line is all there is, so it stays whole.
        ("ATM WITHDRAWAL HDFC INDIRANAGAR", "ATM WITHDRAWAL HDFC INDIRANAGAR", "ATM"),
        ("FLIPKART INTERNET PVT LTD", "FLIPKART INTERNET PVT LTD", None),
    ])
    def test_the_fallback_line_does_not_repeat_the_channel(self, conn, client, raw, fallback, channel):
        row = get_txn(client, add_txn(conn, raw, annotate=False))
        assert (row["payee"], row["channel"]) == (fallback, channel)

    def test_parsed_counterparty_is_shown_as_printed(self, conn, client):
        chai = add_txn(conn, CHAI, annotate=False)
        vpa = add_txn(conn, AMIT, annotate=False)
        assert get_txn(client, chai)["payee"] == "CHAI WALA CORNER"
        assert get_txn(client, vpa)["payee"] == "amit.landlord@oksbi"

    @pytest.mark.parametrize("raw, printed", [
        ("UPI/IRCTC/501234567890/ticket", "IRCTC"),
        ("UPI/DOMINO'S/501234567890/pizza", "DOMINO'S"),
        ("UPI/Chai Wala/501234567890/tea", "Chai Wala"),
    ])
    def test_a_printed_payee_is_never_title_cased(self, conn, client, raw, printed):
        assert get_txn(client, add_txn(conn, raw, annotate=False))["payee"] == printed

    def test_annotation_merchant_beats_the_parsed_name(self, conn, client):
        txn = add_txn(conn, AMIT, merchant="Amit Kumar")
        assert get_txn(client, txn)["payee"] == "Amit Kumar"

    def test_blank_merchant_is_ignored(self, conn, client):
        txn = add_txn(conn, CHAI, merchant="  ")
        assert get_txn(client, txn)["payee"] == "CHAI WALA CORNER"

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
        assert rows[other]["payee"] == "CHAI WALA CORNER"

    def test_never_clears_the_payment_s_own_name(self, conn, client):
        txn = add_txn(conn, AMIT)
        client.put(f"/api/transactions/{txn}/name", json={"name": "Odd one"})
        client.put(f"/api/transactions/{txn}/payee-name", json={"name": "Rent"})
        row = get_txn(client, txn)
        assert row["display_name"] == "Odd one"
        assert row["payee"] == "Odd one"
        assert row["payee_all"] == "Rent"

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


class TestOnePayeeOneName:
    """Rule-sorted payments have no merchant of their own; they still read as
    the payee every other payment to it reads as, in the Ledger and in insights."""

    BESCOM = "UPI/BESCOM/501234567890/electricity"

    def _bill(self, conn, n, *, merchant, month=None):
        return add_txn(conn, self.BESCOM.replace("501234567890", f"5000000000{n}"),
                       date=f"2026-0{n}-05", amount=900, merchant=merchant, category="Bills")

    def test_a_payment_with_no_merchant_reads_as_its_payee(self, conn, client):
        sorted_by_llm = self._bill(conn, 1, merchant="Bescom")
        by_rule = self._bill(conn, 2, merchant=None)
        rows = listed(client)
        assert rows[sorted_by_llm]["payee"] == rows[by_rule]["payee"] == "Bescom"
        assert rows[by_rule]["payee_all"] == "Bescom"
        assert get_txn(client, by_rule)["payee"] == "Bescom"

    def test_the_most_common_merchant_names_the_payee(self, conn, client):
        self._bill(conn, 1, merchant="BESCOM")
        self._bill(conn, 2, merchant="Bescom")
        self._bill(conn, 3, merchant="Bescom")
        by_rule = self._bill(conn, 4, merchant=None)
        assert listed(client)[by_rule]["payee"] == "Bescom"

    def test_a_tie_goes_to_the_same_name_every_time(self, conn, client):
        self._bill(conn, 1, merchant="Bescom")
        self._bill(conn, 2, merchant="BESCOM")
        by_rule = self._bill(conn, 3, merchant=None)
        assert listed(client)[by_rule]["payee"] == "BESCOM"

    def test_this_payment_s_own_merchant_still_beats_the_payee_level_one(self, conn, client):
        self._bill(conn, 1, merchant="Bescom")
        odd = self._bill(conn, 2, merchant="Electricity deposit")
        assert listed(client)[odd]["payee"] == "Electricity deposit"

    def test_a_payee_name_beats_the_payee_level_merchant(self, conn, client):
        a = self._bill(conn, 1, merchant="Bescom")
        client.put(f"/api/transactions/{a}/payee-name", json={"name": "Power"})
        by_rule = self._bill(conn, 2, merchant=None)
        assert listed(client)[by_rule]["payee"] == "Power"

    def test_insights_use_the_same_name_with_no_title_casing_of_their_own(self, conn):
        for n in (1, 2, 3):
            self._bill(conn, n, merchant="Bescom" if n == 1 else None)
        irctc = [add_txn(conn, f"UPI/IRCTC/50000000000{n}/ticket", date=f"2026-0{n}-09", amount=500,
                         category="Travel", annotate=False) for n in (1, 2, 3)]
        assert irctc
        insights = summarize_insights(conn, "2026-03")
        assert {r["name"] for r in insights["recurring"]} == {"Bescom", "IRCTC"}
        assert {m["name"] for m in insights["merchants"]} == {"Bescom", "IRCTC"}

    def test_a_row_shows_the_same_name_in_the_ledger_and_insights(self, conn, client):
        for n in (1, 2, 3):
            self._bill(conn, n, merchant="Bescom" if n == 2 else None)
        ledger = {r["payee_all"] for r in listed(client).values()}
        insights = summarize_insights(conn, "2026-03")
        assert ledger == {"Bescom"} == {r["name"] for r in insights["recurring"]} == {m["name"] for m in insights["merchants"]}


class TestNote:
    """The note line shows what the person typed, not the bank's or app's boilerplate.
    Display only: the stored note, search and embeddings are unchanged."""

    @pytest.mark.parametrize("note, shown", [
        ("Swiggy dinner", "Swiggy dinner"),
        ("chai and samosa", "chai and samosa"),
        ("Monthly SIP", "Monthly SIP"),
        ("to office", "to office"),
        ("running shoes", "running shoes"),
        ("food order", "food order"),
        ("self", "self"),
        ("Salary", "Salary"),
        ("Refund", "Refund"),
        # Notes as the demo ledger's statements print them.
        ("PAYTM QR/chai and samosa", "chai and samosa"),
        ("GROCERIES/Weekly groceries", "Weekly groceries"),
        ("ORDER/Zepto milk eggs bread", "Zepto milk eggs bread"),
        ("SIP/PPFAS FLEXI CAP/Monthly SIP", "Monthly SIP"),
        ("AUTOPAY/Jio monthly plan", "Jio monthly plan"),
        ("RIDE/BANGALORE/to HSR Layout", "to HSR Layout"),
        ("YBL/Haircut and grooming", "Haircut and grooming"),
        ("BROADBAND", None),
        ("AUTOPAY", None),
        ("BROADBAND", None),
        ("UPI Collect Request", None),
        ("upi collect/AUTOPAY", None),
        ("Pay to Merchant", None),
        ("PaymentToZomato", None),
        ("UPI Transaction", None),
        ("Pay via Razorpay", None),
        ("PayViaRazorpay", None),
        ("Oid32479003 3", None),
        ("Oid3192692657@A", None),
        ("rent/UTIB0001234", "rent"),
        ("Oid1234/dinner with friends/ABC123", "dinner with friends"),
        ("", None),
        (None, None),
    ])
    def test_clean_note(self, note, shown):
        assert clean_note(note) == shown

    def test_a_row_shows_the_cleaned_note_and_search_still_finds_the_stored_one(self, conn, client):
        boiler = add_txn(conn, "UPI/ZOMATO/501234567890/AUTOPAY", annotate=False)
        typed = add_txn(conn, "UPI/ZOMATO/501234567891/Swiggy dinner", annotate=False)
        rows = listed(client)
        assert rows[boiler]["note"] is None
        assert rows[typed]["note"] == "Swiggy dinner"
        assert set(listed(client, "&q=autopay")) == {boiler}
        stored = json.loads(conn.execute("SELECT upi_meta FROM transactions WHERE id = ?", (boiler,)).fetchone()[0])
        assert stored["note"] == "AUTOPAY"

    def test_no_note_in_the_parser_fixtures_comes_out_as_boilerplate(self):
        fixtures = Path(__file__).parent / "fixtures" / "description_shapes.jsonl"
        noted = [json.loads(line)["note"] for line in fixtures.read_text().splitlines() if line.strip()]
        shown = [clean_note(n) for n in noted if n]
        assert shown, "the fixtures hold notes"
        for note in shown:
            if note is None:
                continue
            assert not note.lower().startswith(("oid", "upi collect", "pay to ", "paymentto", "upi transact"))
            assert not all(part.strip().isupper() or part.strip().isdigit() for part in note.split("/"))


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


class TestDrillByKey:
    """A row on the Money Map drills by the key it was grouped on, never by a
    name: renaming one payment to the same word must not pull it in."""

    def _setup(self, conn, client):
        rent = [add_txn(conn, AMIT.replace("501234567890", f"50000000000{i}"), date=f"2026-0{i}-01",
                        amount=15000, merchant="Amit Kumar", category="Housing") for i in (1, 2, 3)]
        swiggy = [add_txn(conn, f"UPI/SWIGGY/50000000010{i}/dinner", date=f"2026-0{i}-09", amount=300 + i,
                          merchant="Swiggy", category="Food & Dining") for i in (1, 2, 3)]
        client.put(f"/api/transactions/{rent[0]}/payee-name", json={"name": "Rent"})
        client.put(f"/api/transactions/{swiggy[0]}/name", json={"name": "Rent"})
        return rent, swiggy

    def test_the_recurring_row_carries_its_key_and_drills_to_exactly_its_payments(self, conn, client):
        rent, swiggy = self._setup(conn, client)
        recurring = summarize_insights(conn, "2026-03")["recurring"]
        row = next(r for r in recurring if r["name"] == "Rent")
        assert row["keys"] == [conn.execute(
            "SELECT counterparty_key FROM transactions WHERE id = ?", (rent[0],)).fetchone()[0]]
        drilled = listed(client, "".join(f"&payee_key={k}" for k in row["keys"]))
        assert set(drilled) == set(rent)

    def test_the_merchant_row_carries_its_keys_and_the_summary_agrees(self, conn, client):
        rent, swiggy = self._setup(conn, client)
        merchants = summarize_insights(conn, "2026-03")["merchants"]
        row = next(m for m in merchants if m["name"] == "Rent")
        query = "".join(f"&payee_key={k}" for k in row["keys"])
        assert set(listed(client, query)) == set(rent)
        summary = client.get(f"/api/transactions/summary?{query[1:]}").json()
        assert summary["count"] == 3

    def test_the_key_matches_without_regard_to_case(self, conn, client):
        rent, _ = self._setup(conn, client)
        assert set(listed(client, "&payee_key=amit.landlord@oksbi")) == set(rent)

    def test_merchant_is_the_exact_annotated_merchant_again(self, conn, client):
        rent, swiggy = self._setup(conn, client)
        assert set(listed(client, "&merchant=Swiggy")) == set(swiggy)
        # A name the user gave is not a merchant: it matches nothing here.
        assert listed(client, "&merchant=Rent") == {}


class TestInsightsLabelAtThePayeeLevel:
    def _subscription(self, conn, month, key="NETFLIX"):
        return add_txn(conn, f"UPI/{key}/50{month.replace('-', '')}111/plan", date=f"{month}-05",
                       amount=649, merchant="Netflix", category="Subscriptions")

    def test_renaming_one_of_two_payments_does_not_relabel_the_recurring_row(self, conn, client):
        a, b = (self._subscription(conn, m) for m in ("2026-02", "2026-03"))
        before = summarize_insights(conn, "2026-03")["recurring"]
        client.put(f"/api/transactions/{b}/name", json={"name": "Film night"})
        after = summarize_insights(conn, "2026-03")["recurring"]
        assert [r["name"] for r in before] == [r["name"] for r in after] == ["Netflix"]
        assert after[0]["keys"] == before[0]["keys"]

    def test_renaming_one_of_two_payments_does_not_relabel_the_merchant_row(self, conn, client):
        a, b = (self._subscription(conn, m) for m in ("2026-02", "2026-03"))
        client.put(f"/api/transactions/{b}/name", json={"name": "Film night"})
        rows = summarize_insights(conn, "2026-03")["merchants"]
        assert [m["name"] for m in rows] == ["Netflix"]

    def test_renaming_the_payee_relabels_the_row_and_its_drill_still_lists_every_payment(self, conn, client):
        ids = [self._subscription(conn, m) for m in ("2026-02", "2026-03")]
        client.put(f"/api/transactions/{ids[0]}/payee-name", json={"name": "Movies"})
        row = summarize_insights(conn, "2026-03")["recurring"][0]
        assert row["name"] == "Movies"
        assert set(listed(client, f"&payee_key={row['keys'][0]}")) == set(ids)


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
