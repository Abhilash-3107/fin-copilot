"""Merchant catalog, rule matching, the LLM view, and fact-gated categories
(findings/2026-11-09-pipeline-robustness/04)."""
from __future__ import annotations

import json
import sqlite3

import pytest

from finance_copilot.config import settings
from finance_copilot.db.connection import init_db
from finance_copilot.parsers.upi import parse_upi_description
from finance_copilot.pipeline.counterparty import normalize_identity
from finance_copilot.pipeline.merchant_catalog import (
    DISAMBIGUATION_RULES,
    MERCHANT_RULES,
    strip_payment_app,
)
from finance_copilot.pipeline.rules import _pattern_matches, apply_rules


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s', 't', '1', '2026-06')"
    )
    conn.commit()
    return conn


def _txn(desc: str, dc: str = "debit") -> dict:
    return {"id": "t", "raw_description": desc, "debit_credit": dc,
            "upi_meta": parse_upi_description(desc, settings.upi_noise_keywords),
            "counterparty_key": normalize_identity(desc)}


def _rule(desc, dc="debit"):
    return apply_rules(_txn(desc, dc))


class TestCatalogIntegrity:
    def test_every_rule_targets_a_seeded_category(self):
        conn = _conn()
        tree: dict[str, set[str]] = {}
        for r in conn.execute(
            "SELECT p.name AS parent, c.name AS child FROM categories c LEFT JOIN categories p ON p.id = c.parent_id"
        ):
            if r["parent"] is None:
                tree.setdefault(r["child"], set())
            else:
                tree.setdefault(r["parent"], set()).add(r["child"])
        for rule in [*MERCHANT_RULES, *DISAMBIGUATION_RULES]:
            assert rule.category in tree, rule
            if rule.subcategory is not None:
                assert rule.subcategory in tree[rule.category], rule

    def test_no_pattern_matches_inside_another_merchants_name(self):
        # A pattern of one entry found word-bounded inside another entry's
        # pattern means that other merchant's payments could hit the wrong rule
        # first. (Patterns of the same entry may nest: 'ola' / 'ola cabs'.)
        clashes = [
            (p, q)
            for i, a in enumerate(MERCHANT_RULES) for p in a.patterns
            for j, b in enumerate(MERCHANT_RULES) if i != j for q in b.patterns
            if p != q and _pattern_matches(p, q) and i < j
        ]
        assert clashes == []


class TestMatching:
    @pytest.mark.parametrize(("desc", "category", "merchant"), [
        ("UPI/URBANCOMPANY/122321555487/UPIInten t", "Personal Care", "Urban Company"),
        ("UPI/ROPPEN TRANSPOR/122391726301/PaymentToROPPE N", "Transport", "Rapido"),
        ("UPI/ROPPEN TRANSPO/AIRP/125495154676/PaymentToRO P", "Transport", "Rapido"),
        ("UPI/Licious/121979067994/UPI Collect", "Food & Dining", "Licious"),
        ("UPI/IBIBO Group Pri/103477659082/Pay via Razorpa", "Travel", "Goibibo"),
        ("UPI/PVR INOX Limite/121440823398/UPI", "Entertainment", "PVR INOX"),
        ("UPI/Agoda/YESB/208485246070/Oid32479003 3", "Travel", "Agoda"),
        ("UPI/BUNDL/123456789012/Swiggy order PhonePe", "Food & Dining", "Swiggy"),
    ])
    def test_merchants(self, desc, category, merchant):
        ann = _rule(desc)
        assert ann is not None, desc
        assert (ann.category, ann.merchant) == (category, merchant)

    @pytest.mark.parametrize("desc", [
        "UPI/CARLO ANTONIO/123456789012/UPI",       # not 'car loan'
        "UPI/SALCRAFT DESIGNS/123456789012/UPI",    # not 'sal cr'
        "UPI/GOFIRSTCHOICE MART/123456789012/UPI",  # not 'go first'
        "UPI/OLAMIDE STORES/123456789012/UPI",      # 'ola' stays word-bounded
        "UPI/RAVI KUMAR/HDFC/123456789012/UPI",     # a bank prefix is not a merchant
        "UPI/TASTY MUNCH LL/124237509246/Pay To BharatPe",  # the payment app is not the merchant
        "UPI/AGODAX TRAVELS/123456789012/UPI",
        "UPI/ELECTRICITYB/123456789012/UPI",
        "UPI/ETERNAL LIMITED/122873980363/PayviaRazorpay",  # Zomato, Blinkit, or District: left to history
    ])
    def test_no_false_positives(self, desc):
        assert _rule(desc) is None

    def test_notes_still_match(self):
        assert _rule("UPI/RAVI KUMAR SHA/123456789012/rent payment").category == "Housing"

    def test_non_upi_lines(self):
        assert _rule("NEFT HDFCH00714141560 INDSTOCKS PVT LTD", "credit").category == "Transfers"


class TestDirections:
    def test_refund_from_a_merchant_is_not_spending(self):
        assert _rule("UPI/Practo technolo/103137030572/Refund", "credit") is None

    def test_paying_someone_a_salary_is_not_income(self):
        assert _rule("UPI/SURESH MAID/123456789012/salary oct", "debit") is None

    def test_salary_credit_is_income(self):
        assert _rule("NEFT SALARY ACME CORP", "credit").category == "Income"


class TestPaymentApps:
    @pytest.mark.parametrize(("note", "rest"), [
        ("Pay To BharatPe", None), ("Pay via Razorpa", None), ("PayviaRazorpay", None),
        ("Verified Paytm", None), ("Swiggy order PhonePe", "Swiggy order"),
        ("Pay Udemy course", "Pay Udemy course"), ("movie tickets", "movie tickets"), (None, None),
    ])
    def test_only_the_payment_app_phrase_is_removed(self, note, rest):
        assert strip_payment_app(note) == rest


class TestLlmView:
    def _prompt(self, desc, dc="debit"):
        from finance_copilot.pipeline.llm import _build_user_prompt

        txn = {**_txn(desc, dc), "amount": 100.0, "txn_date": "2026-06-19"}
        return _build_user_prompt(txn, ["Entertainment", "Finances", "Uncategorized"])

    def test_bank_prefix_ref_and_value_date_hidden(self):
        prompt = self._prompt("UPI/NESCO LIMITED/HDFC/125009482035/UPI (Value Date: 20-Jun-26)")
        assert "UPI payment to NESCO LIMITED" in prompt
        assert "HDFC" not in prompt and "125009482035" not in prompt and "Value Date" not in prompt

    @pytest.mark.parametrize("desc", [
        "UPI/RAMESH KUMAR/412345678901/NO REMARKS",
        "UPI/RAMESH KUMAR/412345678901/ramesh@okaxis",
        "UPI/TASTY MUNCH LL/124237509246/Pay To BharatPe",
    ])
    def test_boilerplate_vpa_and_payment_apps_never_reach_the_prompt(self, desc):
        assert "UPI Note" not in self._prompt(desc)

    def test_meaningful_note_and_direction(self):
        assert "UPI Note: movie tickets" in self._prompt("UPI/RAVI KUMAR SHA/123456789012/movie tickets")
        assert "UPI payment from RAVI KUMAR SHA" in self._prompt("UPI/RAVI KUMAR SHA/123456789012/UPI", "credit")

    def test_catch_all_is_the_installed_one(self):
        assert "answer Uncategorized" in self._prompt("UPI/SOME SHOP/123456789012/UPI")

    def test_fewshot_examples_keep_stored_notes(self):
        from finance_copilot.pipeline.llm import _build_fewshot_user_prompt

        example = {"raw_description": "NEFT ACME CORP", "debit_credit": "credit", "upi_note": "march invoice",
                   "txn_date": "2026-03-01", "amount": 10.0, "category": "Income"}
        txn = {**_txn("UPI/SOME SHOP/123456789012/UPI"), "amount": 1.0, "txn_date": "2026-06-01"}
        assert "UPI Note: march invoice" in _build_fewshot_user_prompt(txn, ["Income"], [example])


class TestSelfTransferGate:
    def _row(self, conn, txn_id, name, *, day, label=None):
        desc = f"UPI/{name}/12345678{day:04d}/UPI"
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
            "VALUES (?, 's', ?, 50, 'debit', ?, ?)",
            (txn_id, f"2026-06-{day:02d}", desc, name.upper()),
        )
        if label:
            conn.execute("INSERT INTO annotations (id, transaction_id, category, confidence, source) "
                         "VALUES (?, ?, ?, 1.0, 'manual')", (f"a_{txn_id}", txn_id, label))
        conn.commit()
        return dict(conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone())

    def _gate(self, conn, txn, as_of):
        from finance_copilot.pipeline.annotate import gate_fact_categories

        return gate_fact_categories(conn, txn, "Self Transfers", "Self Transfer", 0.9, as_of=as_of)

    def test_unverified_self_transfer_becomes_peer_transfer_for_review(self):
        conn = _conn()
        txn = self._row(conn, "t", "Mr Imran Shaik", day=1)
        cat, sub, conf, cap = self._gate(conn, txn, txn["txn_date"])
        assert (cat, sub, cap) == ("Transfers", "Peer Transfer", "self_transfer_unverified")
        assert conf <= settings.rag_defer_confidence_cap

    def test_own_verified_labels_for_the_payee_verify(self):
        conn = _conn()
        self._row(conn, "p0", "RAVI KUMAR SHA", day=1, label="Self Transfers")
        txn = self._row(conn, "t", "RAVI KUMAR SHA", day=20)
        assert self._gate(conn, txn, txn["txn_date"])[3] is None

    def test_future_labels_do_not_verify(self):
        # Causal like every history read: a replay can't use labels made later.
        conn = _conn()
        txn = self._row(conn, "t", "RAVI KUMAR SHA", day=1)
        self._row(conn, "p0", "RAVI KUMAR SHA", day=10, label="Self Transfers")
        assert self._gate(conn, txn, txn["txn_date"])[3] == "self_transfer_unverified"

    def test_mostly_other_labels_do_not_verify(self):
        conn = _conn()
        self._row(conn, "p0", "RAVI KUMAR SHA", day=1, label="Self Transfers")
        self._row(conn, "p1", "RAVI KUMAR SHA", day=2, label="Transfers")
        txn = self._row(conn, "t", "RAVI KUMAR SHA", day=20)
        assert self._gate(conn, txn, txn["txn_date"])[3] == "self_transfer_unverified"

    def test_other_categories_untouched(self):
        from finance_copilot.pipeline.annotate import gate_fact_categories

        conn = _conn()
        txn = self._row(conn, "t", "SOME SHOP", day=1)
        assert gate_fact_categories(conn, txn, "Shopping", None, 0.7, as_of=None) == ("Shopping", None, 0.7, None)

    def test_person_rule_with_self_relationship(self):
        from finance_copilot.pipeline.annotate import _match_known_person

        txn = {"id": "t", "raw_description": "UPI/RAVI KUMAR SHA/123456789012/self", "upi_meta": None,
               "counterparty_key": "RAVI KUMAR SHA"}
        ann = _match_known_person(txn, [("Me", "ravi", "self")])
        assert (ann.category, ann.subcategory) == ("Self Transfers", "Self Transfer")

    def test_rag_direct_neighbour_cannot_make_a_self_transfer(self):
        from unittest.mock import patch

        from finance_copilot.pipeline.annotate import _try_rag_annotation

        conn = _conn()
        self._row(conn, "donor", "RAVI KUMAR SHA", day=1, label="Self Transfers")
        txn = self._row(conn, "t", "RAVI KUMAR SHAH", day=20)  # a sibling with the same surname
        with patch("finance_copilot.pipeline.annotate.get_embedding_single", return_value=[0.1] * 768), \
             patch("finance_copilot.pipeline.annotate.find_similar",
                   return_value=[{"transaction_id": "donor", "distance": 0.0}]):
            ann, trace = _try_rag_annotation(conn, txn, ["Self Transfers", "Transfers"], as_of=txn["txn_date"])
        assert (ann.source, ann.category) == ("rag_direct", "Transfers")
        assert "self_transfer_unverified" in trace.caps_applied
        assert json.loads(trace.model_dump_json())["final_confidence"] <= settings.rag_defer_confidence_cap
