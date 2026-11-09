"""Review queue groups, group answers, payee kind, and review policies
(findings/2026-11-09-pipeline-robustness/05)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from finance_copilot.config import settings
from finance_copilot.db.connection import get_connection, init_db
from finance_copilot.db.queries.review_policies import create_policy, list_policies, match_policy
from finance_copilot.parsers.payee import BUSINESS, PERSON, UNKNOWN, classify_payee
from finance_copilot.pipeline.counterparty import normalize_identity
from finance_copilot.pipeline.review_queue import group_review_queue


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    init_db(c)
    c.execute("INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s', 't', '1', '2026-06')")
    c.commit()
    return c


def _txn(conn, txn_id, name, amount=60.0, *, dc="debit", day=1, label=None, source="llm", confidence=0.15):
    desc = f"UPI/{name}/12345{day:07d}/UPI"
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
        "VALUES (?, 's', ?, ?, ?, ?, ?)",
        (txn_id, f"2026-06-{day:02d}", amount, dc, desc, normalize_identity(desc)),
    )
    if label:
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, confidence, source) VALUES (?, ?, ?, ?, ?)",
            (f"a_{txn_id}", txn_id, label, confidence, source),
        )
    conn.commit()
    return dict(conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone())


class TestPayeeKind:
    @pytest.mark.parametrize("name", [
        "ROHIT YADAV", "Kunal Gupta", "Mr Imran Shaik", "MD SALIM", "NITIN R PATIL",
        "ROHAN SUDHIR K", "VARUN RAJESHKU", "Mohammad Arif",
    ])
    def test_people(self, name):
        assert classify_payee(name) == PERSON

    @pytest.mark.parametrize("name", [
        "M S KINGSTON PR", "SHANTI ENTERP", "SHREE AGRO AND", "Blue Tokai Cof", "NESCO LIMITED",
        "TASTY MUNCH LL", "QXRT7", "A K SUPER MARKE", "Oberoi Mall", "ROPPEN TRANSPOR",
    ])
    def test_businesses(self, name):
        assert classify_payee(name) == BUSINESS

    @pytest.mark.parametrize("name", [
        # Shops named after people are the policy's most dangerous false positive.
        "BALAJI DAIRY", "SHARMA DAIRY", "RAJESH ELECTRICALS", "KRISHNA FRUITS", "SRI SAI TIFFINS",
        "SRI VENKATESHWARA", "AMIT MEDICOS", "GUPTA CHAAT", "AGARWAL PACKERS", "MAURYA SAREES",
        "VERMA OPTICAL", "AMARON BATTERIES", "KHANA KHAZANA", "PARAMOUNT TAILORS", "SHIVAJI PARK",
        "GANESHA PAN", "ANANDA BHAVAN",
        # One bare word, or words that aren't names.
        "FIROZ", "Licious", "zentile", "OBEROIFC tucks", "SUNSET TO DUSKS", "", None,
    ])
    def test_unknown_when_unsure(self, name):
        assert classify_payee(name) == UNKNOWN


class TestPolicyMatch:
    def _policies(self, conn, *limits):
        for limit, category in limits:
            create_policy(conn, "small_personal_payment", limit, category, None)
        conn.commit()
        return list_policies(conn, enabled_only=True)

    def test_small_payment_to_a_stranger(self, conn):
        policies = self._policies(conn, (500, "Transport"))
        txn = _txn(conn, "t", "ROHIT YADAV", 50)
        assert match_policy(conn, policies, txn, as_of=txn["txn_date"])["category"] == "Transport"

    @pytest.mark.parametrize(("name", "amount", "dc"), [
        ("ROHIT YADAV", 900, "debit"),       # above the limit
        ("ROHIT YADAV", 50, "credit"),       # money in
        ("SHANTI ENTERP", 50, "debit"),      # a business
        ("BALAJI DAIRY", 50, "debit"),       # a shop named after a person
        ("FIROZ", 50, "debit"),              # can't tell
    ])
    def test_out_of_scope(self, conn, name, amount, dc):
        policies = self._policies(conn, (500, "Transport"))
        txn = _txn(conn, "t", name, amount, dc=dc)
        assert match_policy(conn, policies, txn, as_of=txn["txn_date"]) is None

    def test_most_specific_policy_wins(self, conn):
        policies = self._policies(conn, (500, "Transport"), (100, "Food & Dining"))
        txn = _txn(conn, "t", "ROHIT YADAV", 60)
        assert match_policy(conn, policies, txn, as_of=txn["txn_date"])["category"] == "Food & Dining"

    def test_payee_with_other_verified_history_is_excluded(self, conn):
        policies = self._policies(conn, (500, "Transport"))
        _txn(conn, "old", "ROHIT YADAV", 80, day=1, label="Food & Dining", source="manual", confidence=1.0)
        txn = _txn(conn, "t", "ROHIT YADAV", 50, day=9)
        assert match_policy(conn, policies, txn, as_of=txn["txn_date"]) is None

    def test_history_is_causal_and_per_direction(self, conn):
        policies = self._policies(conn, (500, "Transport"))
        _txn(conn, "refund", "ROHIT YADAV", 80, dc="credit", day=1, label="Income", source="manual", confidence=1.0)
        _txn(conn, "later", "ROHIT YADAV", 80, day=20, label="Food & Dining", source="manual", confidence=1.0)
        txn = _txn(conn, "t", "ROHIT YADAV", 50, day=9)
        assert match_policy(conn, policies, txn, as_of=txn["txn_date"])["category"] == "Transport"

    def test_pipeline_applies_policy_before_any_model(self, conn):
        from finance_copilot.pipeline.annotate import rule_annotation

        self._policies(conn, (500, "Transport"))
        txn = _txn(conn, "t", "Mr Imran Shaik", 20)
        ann, trace = rule_annotation(conn, txn, [], as_of=txn["txn_date"])
        assert (ann.category, ann.source, trace.stage) == ("Transport", "policy", "policy")


class TestGrouping:
    def _item(self, i, key, amount, conf=0.1, dc="debit", date="2026-06-01"):
        return {"annotation_id": str(i), "transaction_id": str(i), "counterparty_key": key, "debit_credit": dc,
                "raw_description": f"UPI/{key}/12345678901{i}/UPI", "amount": amount, "confidence": conf,
                "txn_date": date}

    def test_groups_are_adjacent_oldest_first(self):
        out = group_review_queue([
            self._item(1, "SOLO SHOP", 300),
            self._item(2, "OBEROIFC", 150, date="2026-06-03"),
            self._item(3, "OBEROIFC", 150, date="2026-06-02"),
            self._item(4, "OBEROIFC", 150, dc="credit"),
        ])
        assert [i["annotation_id"] for i in out] == ["3", "2", "1", "4"]
        assert [i["group_size"] for i in out] == [2, 2, 1, 1]  # a refund is its own question

    def test_a_large_single_unknown_is_not_buried_by_repeats(self):
        out = group_review_queue(
            [self._item(i, "SMALL PAYEE", 200, conf=0.15) for i in range(3)] + [self._item(9, "BIG ONE", 50000, conf=0.3)]
        )
        assert out[0]["annotation_id"] == "9"


class TestApi:
    @pytest.fixture
    def client(self, conn):
        from finance_copilot.api.deps import get_db as api_get_db
        from finance_copilot.main import app

        app.dependency_overrides[api_get_db] = lambda: conn
        with patch("finance_copilot.api.routes.annotations.embed_transactions"):
            yield TestClient(app)
        app.dependency_overrides.clear()

    def test_group_answer_applies_the_card_label_with_per_item_feedback(self, client, conn):
        _txn(conn, "t0", "OBEROIFC tucksh", 100, day=1, label="Food & Dining")
        _txn(conn, "t1", "OBEROIFC tucksh", 100, day=2, label="Shopping")
        resp = client.post("/api/annotations/bulk-label", json={
            "annotation_ids": ["a_t0", "a_t1"], "label": {"category": "Food & Dining"},
        })
        assert resp.json()["updated"] == 2
        assert {tuple(r) for r in conn.execute("SELECT category, source, confidence FROM annotations")} == {
            ("Food & Dining", "manual", 1.0)
        }
        stats = {r["category"]: dict(r) for r in conn.execute("SELECT * FROM feedback_stats WHERE source = 'llm'")}
        assert stats["Food & Dining"]["confirmed"] == 1 and stats["Shopping"]["corrected"] == 1

    def test_group_answer_needs_a_label(self, client, conn):
        _txn(conn, "t0", "OBEROIFC tucksh", 100, day=1, label="Food & Dining")
        assert client.post("/api/annotations/bulk-label", json={"annotation_ids": ["a_t0"]}).status_code == 422

    def test_review_queue_carries_group_fields(self, client, conn):
        _txn(conn, "t0", "OBEROIFC tucksh", 100, day=1, label="Miscellaneous")
        _txn(conn, "t1", "OBEROIFC tucksh", 100, day=20, label="Miscellaneous")
        items = client.get("/api/annotations/review-queue").json()
        assert {i["group_size"] for i in items} == {2}

    def test_policy_preview_create_and_delete(self, client, conn):
        _txn(conn, "t0", "ROHIT YADAV", 40, day=1, label="Miscellaneous")
        _txn(conn, "t1", "Mr Imran Shaik", 20, day=2, label="Self Transfers")
        _txn(conn, "t2", "SHANTI ENTERP", 100, day=3, label="Miscellaneous")
        body = {"max_amount": 500, "category": "Transport", "subcategory": "Cab & Auto"}
        assert client.post("/api/policies/preview", json=body).json() == {"matches": 2}

        created = client.post("/api/policies", json=body).json()
        assert created["resolved"] == 2
        got = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT transaction_id, category, source FROM annotations")}
        assert got == {"t0": ("Transport", "policy"), "t1": ("Transport", "policy"), "t2": ("Miscellaneous", "llm")}

        # Deleting the policy hands its labels back to review.
        assert client.delete(f"/api/policies/{created['id']}").json() == {"returned_to_review": 2}
        confs = [r[0] for r in conn.execute("SELECT confidence FROM annotations WHERE source = 'policy'")]
        assert confs and all(c < settings.confidence_threshold for c in confs)

    def test_policy_labels_are_not_trusted_evidence(self):
        from finance_copilot.pipeline.sources import MACHINE_SOURCES, TRUSTED_SOURCES

        assert "policy" in MACHINE_SOURCES and "policy" not in TRUSTED_SOURCES

    def test_policy_validation(self, client):
        assert client.post("/api/policies", json={"max_amount": 0, "category": "Transport"}).status_code == 422
        assert client.post("/api/policies", json={"max_amount": 50, "category": "Nope"}).status_code == 422
        assert client.post("/api/policies", json={"max_amount": 50, "category": "Transport", "kind": "x"}).status_code == 422
        created = client.post("/api/policies", json={"max_amount": 50, "category": "Transport"}).json()
        assert client.patch(f"/api/policies/{created['id']}", json={"max_amount": 80}).json()["max_amount"] == 80
        assert client.patch(f"/api/policies/{created['id']}", json={"enabled": False}).json()["enabled"] is False
        assert client.delete(f"/api/policies/{created['id']}").status_code == 200
        assert client.get("/api/policies").json() == []


class TestUserRulesIgnoreBankEncoding:
    def test_bank_prefix_does_not_trigger_a_user_rule(self):
        from finance_copilot.db.queries.user_rules import match_user_rule

        rules = [{"pattern": "hdfc", "enabled": True}]
        new_layout = {"raw_description": "UPI/RAVI KUMAR SHA/HDFC/123456789012/UPI", "upi_meta": None}
        assert match_user_rule(rules, new_layout) is None
        card = {"raw_description": "HDFC CREDIT CARD PAYMENT", "upi_meta": None}
        assert match_user_rule(rules, card) is not None

    def test_a_rule_on_the_vpa_still_fires(self):
        from finance_copilot.db.queries.user_rules import match_user_rule

        rules = [{"pattern": "landlord@okicici", "enabled": True}]
        txn = {"raw_description": "UPI/landlord@okicici/123456789012/rent", "upi_meta": None}
        assert match_user_rule(rules, txn) is not None
