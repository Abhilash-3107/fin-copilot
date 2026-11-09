"""Merchant catalog, normalized rule matching, LLM view, and fact-gated
categories (findings/2026-11-09-pipeline-robustness/04)."""
from __future__ import annotations

import json
import sqlite3

import pytest

from finance_copilot.config import settings
from finance_copilot.db.connection import init_db
from finance_copilot.parsers.description import describe_for_llm, is_aggregator_note
from finance_copilot.pipeline.merchant_catalog import DISAMBIGUATION_RULES, MERCHANT_RULES
from finance_copilot.pipeline.rules import apply_rules


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


def _rule(desc, note=None):
    meta = json.dumps({"note": note}) if note else None
    return apply_rules({"id": "t", "raw_description": desc, "upi_meta": meta})


class TestCatalogIntegrity:
    def test_every_rule_targets_a_seeded_category(self):
        conn = _conn()
        tree = {}
        for r in conn.execute(
            "SELECT p.name AS parent, c.name AS child FROM categories c "
            "LEFT JOIN categories p ON p.id = c.parent_id"
        ):
            if r["parent"] is None:
                tree.setdefault(r["child"], set())
            else:
                tree.setdefault(r["parent"], set()).add(r["child"])
        for rule in [*MERCHANT_RULES, *DISAMBIGUATION_RULES]:
            assert rule.category in tree, rule
            if rule.subcategory is not None:
                assert rule.subcategory in tree[rule.category], rule


class TestNormalizedMatching:
    @pytest.mark.parametrize(("desc", "category", "merchant"), [
        ("UPI/URBANCOMPANY/122321555487/UPIInten t", "Personal Care", "Urban Company"),
        ("UPI/ROPPEN TRANSPOR/122391726301/PaymentToROPPE N", "Transport", "Rapido"),
        ("UPI/ROPPEN TRANSPO/AIRP/125495154676/PaymentToRO P", "Transport", "Rapido"),
        ("UPI/Licious/121979067994/UPI Collect", "Food & Dining", "Licious"),
        ("UPI/IBIBO Group Pri/103477659082/Pay via Razorpa", "Travel", "Goibibo"),
        ("UPI/PVR INOX Limite/121440823398/UPI", "Entertainment", "PVR INOX"),
        ("UPI/Agoda/YESB/208485246070/Oid32479003 3", "Travel", "Agoda"),
    ])
    def test_legal_entities_and_compacted_names(self, desc, category, merchant):
        ann = _rule(desc)
        assert ann is not None, desc
        assert (ann.category, ann.merchant) == (category, merchant)

    @pytest.mark.parametrize("desc", [
        "UPI/OLAMIDE STORES/123456789012/UPI",   # 'ola' stays word-bounded
        "UPI/RAVI KUMAR/HDFC/123456789012/UPI",  # bank prefix is not a merchant
        "UPI/TASTY MUNCH LL/124237509246/Pay To BharatPe",  # payment app is not the merchant
        "UPI/AGODAX TRAVELS/123456789012/UPI",   # unrelated name sharing a short prefix
    ])
    def test_no_false_positives(self, desc):
        assert _rule(desc) is None

    def test_ambiguous_entity_left_to_history(self):
        # Eternal is Zomato, Blinkit, and District: no single category.
        assert _rule("UPI/ETERNAL LIMITED/122873980363/PayviaRazorpay") is None

    def test_merchant_refund_is_not_spending(self):
        refund = apply_rules({"id": "t", "raw_description": "UPI/Practo technolo/103137030572/Refund",
                              "upi_meta": None, "debit_credit": "credit"})
        assert refund is None
        salary = apply_rules({"id": "t", "raw_description": "NEFT SALARY ACME CORP", "upi_meta": None,
                              "debit_credit": "credit"})
        assert salary is not None and salary.category == "Income"

    def test_notes_still_match(self):
        ann = _rule("UPI/RAVI KUMAR SHA/123456789012/rent payment")
        assert ann is not None and ann.category == "Housing"

    def test_non_upi_lines_unchanged(self):
        ann = _rule("NEFT HDFCH00714141560 INDSTOCKS PVT LTD")
        assert ann is not None and ann.category == "Transfers"


class TestLlmView:
    def test_bank_prefix_ref_and_value_date_hidden(self):
        desc, note = describe_for_llm("UPI/NESCO LIMITED/HDFC/125009482035/UPI (Value Date: 20-Jun-26)", "debit")
        assert desc == "UPI payment to NESCO LIMITED"
        assert note is None

    def test_payment_app_note_dropped_meaningful_note_kept(self):
        assert describe_for_llm("UPI/TASTY MUNCH LL/124237509246/Pay To BharatPe", "debit")[1] is None
        assert describe_for_llm("UPI/RAVI KUMAR SHA/123456789012/movie tickets", "debit")[1] == "movie tickets"

    def test_direction_and_reversal(self):
        assert describe_for_llm("UPI/RAVI KUMAR SHA/123456789012/UPI", "credit")[0] == "UPI payment from RAVI KUMAR SHA"
        assert describe_for_llm("REV-UPI/Medkart Pharm/115697050961/Refund", "credit")[0] == (
            "UPI reversal (refund) from Medkart Pharm"
        )

    def test_unknown_layout_passes_through(self):
        assert describe_for_llm("NEFT-AXIS-ACME CORP", "credit") == ("NEFT-AXIS-ACME CORP", None)

    def test_user_prompt_never_shows_bank_prefix(self):
        from finance_copilot.pipeline.llm import _build_user_prompt

        prompt = _build_user_prompt(
            {"raw_description": "UPI/NESCO LIMITED/HDFC/125009482035/UPI", "debit_credit": "debit",
             "amount": 3649.0, "txn_date": "2026-06-19", "upi_meta": None},
            ["Entertainment", "Finances"],
        )
        assert "HDFC" not in prompt and "125009482035" not in prompt
        assert "UPI payment to NESCO LIMITED" in prompt

    @pytest.mark.parametrize(("note", "expected"), [
        ("Pay To BharatPe", True), ("Pay via Razorpa", True), ("PayviaRazorpay", True),
        ("Verified Paytm", True), ("movie tickets", False), (None, False),
    ])
    def test_aggregator_notes(self, note, expected):
        assert is_aggregator_note(note) is expected


class TestSelfTransferGate:
    def _txn(self, conn, txn_id, name, *, day=1):
        desc = f"UPI/{name}/12345678{day:04d}/UPI"
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
            "VALUES (?, 's', ?, 50, 'debit', ?, ?)",
            (txn_id, f"2026-06-{day:02d}", desc, name.upper()),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone())

    def test_unverified_self_transfer_becomes_peer_transfer_for_review(self):
        from finance_copilot.pipeline.annotate import gate_fact_categories

        conn = _conn()
        txn = self._txn(conn, "t", "Mr Imran Shaik")
        cat, sub, conf, cap = gate_fact_categories(conn, txn, "Self Transfers", "Self Transfer", 0.9)
        assert (cat, sub, cap) == ("Transfers", "Peer Transfer", "self_transfer_unverified")
        assert conf <= settings.rag_defer_confidence_cap

    def test_self_person_verifies(self):
        from finance_copilot.db.queries.people import create_person
        from finance_copilot.pipeline.annotate import gate_fact_categories

        conn = _conn()
        create_person(conn, "Me (HDFC)", upi="ravi", relationship="self")
        txn = self._txn(conn, "t", "RAVI KUMAR SHA")
        assert gate_fact_categories(conn, txn, "Self Transfers", "Self Transfer", 0.9)[3] is None

    def test_own_verified_history_verifies(self):
        from finance_copilot.pipeline.annotate import gate_fact_categories

        conn = _conn()
        prior = self._txn(conn, "p", "RAVI KUMAR SHA", day=1)
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, confidence, source) "
            "VALUES ('a', ?, 'Self Transfers', 1.0, 'manual')", (prior["id"],),
        )
        conn.commit()
        txn = self._txn(conn, "t", "RAVI KUMAR SHA", day=5)
        assert gate_fact_categories(conn, txn, "Self Transfers", "Self Transfer", 0.9)[3] is None

    def test_other_categories_untouched(self):
        from finance_copilot.pipeline.annotate import gate_fact_categories

        conn = _conn()
        txn = self._txn(conn, "t", "SOME SHOP")
        assert gate_fact_categories(conn, txn, "Shopping", None, 0.7) == ("Shopping", None, 0.7, None)

    def test_person_rule_with_self_relationship(self):
        from finance_copilot.pipeline.annotate import _match_known_person

        txn = {"id": "t", "raw_description": "UPI/RAVI KUMAR SHA/123456789012/self", "upi_meta": None,
               "counterparty_key": "RAVI KUMAR SHA"}
        ann = _match_known_person(txn, [("Me", "ravi", "self")])
        assert ann is not None
        assert (ann.category, ann.subcategory) == ("Self Transfers", "Self Transfer")
