"""Description grammar, truncation-tolerant identity, and versioned re-derivation.

The contract under test: a bank changing its description layout must never
split one payee into two identities or two embeddings, and must never merge two
different payees. See findings/2026-11-09-pipeline-robustness/01.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from finance_copilot.config import settings
from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.annotations import insert_annotation
from finance_copilot.db.queries.transactions import insert_transaction
from finance_copilot.models.annotation import Annotation
from finance_copilot.models.transaction import Transaction
from finance_copilot.parsers.description import (
    DESCRIPTION_PARSER_VERSION,
    is_known_shape,
    parse_description,
)
from finance_copilot.pipeline.counterparty import (
    KeyResolver,
    counterparty_identity,
    normalize_identity,
    pick_canonical_key,
    resolve_counterparty_key,
)
from finance_copilot.pipeline.derived import (
    PARSER_VERSION_KEY,
    embeddings_stale,
    reconcile_derived_data,
    rederive_descriptions,
)
from finance_copilot.pipeline.embed import build_embed_text

NOISE = settings.upi_noise_keywords
FIXTURES = Path(__file__).parent / "fixtures" / "description_shapes.jsonl"
SHAPES = [json.loads(line) for line in FIXTURES.read_text().splitlines() if line.strip()]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    conn.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month) "
        "VALUES ('s1', 'test', '1', '2026-06')"
    )
    conn.commit()
    return conn


def _ingest(conn, txn_id: str, desc: str, day: int = 1, month: int = 6) -> None:
    insert_transaction(conn, Transaction(
        id=txn_id, statement_id="s1", txn_date=date(2026, month, day), amount=100.0,
        debit_credit="debit", raw_description=desc,
    ))


class TestGrammarFixtures:
    @pytest.mark.parametrize("case", SHAPES, ids=[c["raw"][:40] for c in SHAPES])
    def test_fixture_parses_as_expected(self, case):
        p = parse_description(case["raw"], NOISE)
        assert p.rail == case["rail"]
        assert p.shape == case["shape"]
        assert p.counterparty == case["counterparty"]
        assert p.bank_code == case["bank_code"]
        assert p.ref == case["ref"]
        assert p.note == case["note"]
        assert p.value_date == case["value_date"]
        if "vpa" in case:
            assert p.vpa == case["vpa"]

    @pytest.mark.parametrize("case", [c for c in SHAPES if c["rail"] != "other"],
                             ids=lambda c: c["raw"][:40])
    def test_every_upi_fixture_is_a_known_shape(self, case):
        assert is_known_shape(parse_description(case["raw"], NOISE))

    def test_unknown_qualifier_is_flagged_not_dropped(self):
        p = parse_description("UPI/SOME SHOP/XY12/QQ/123456789012/UPI", NOISE)
        assert p.shape == "upi/name/?/ref/note"
        assert p.counterparty == "SOME SHOP"
        assert p.ref == "123456789012"
        assert not is_known_shape(p)

    def test_no_ref_is_unrecognized_but_keeps_payee(self):
        p = parse_description("UPI/NETFLIX/AUTOPAY", NOISE)
        assert p.shape == "unrecognized"
        assert p.counterparty == "NETFLIX"

    def test_never_raises_on_garbage(self):
        for raw in (None, "", "/", "UPI", "UPI/", "UPI//", "UPI/ /123456/x", "////"):
            parse_description(raw, NOISE)


class TestLayoutInvariance:
    """Same payee, old and new Kotak layout: one identity, one embedding text."""

    PAIRS = [
        ("UPI/OBEROIFC tucksh/118364694521/UPI", "UPI/OBEROIFC tucks/YESB/125146338066/UPI"),
        ("UPI/ROPPEN TRANSPOR/122391726301/UPI", "UPI/ROPPEN TRANSPO/AIRP/125495154676/UPI"),
        ("UPI/Blue Tokai Cof/121449855475/UPI", "UPI/Blue Tokai Cof/YESB/124907113263/UPI"),
    ]

    @pytest.mark.parametrize(("old", "new"), PAIRS)
    def test_same_key_after_ingest(self, old, new):
        conn = _conn()
        _ingest(conn, "a", old, day=1)
        _ingest(conn, "b", new, day=20)
        keys = {r[0] for r in conn.execute("SELECT counterparty_key FROM transactions")}
        assert len(keys) == 1

    @pytest.mark.parametrize(("old", "new"), PAIRS)
    def test_same_embed_text_after_ingest(self, old, new):
        conn = _conn()
        _ingest(conn, "a", old, day=1)
        _ingest(conn, "b", new, day=20)
        rows = [dict(r) for r in conn.execute("SELECT * FROM transactions ORDER BY id")]
        assert build_embed_text(rows[0]) == build_embed_text(rows[1])

    @pytest.mark.parametrize("case", [c for c in SHAPES if c["rail"] != "other"],
                             ids=lambda c: c["raw"][:40])
    def test_noise_never_reaches_embed_text(self, case):
        p = parse_description(case["raw"], NOISE)
        text = build_embed_text({"debit_credit": "debit", "raw_description": case["raw"]})
        assert p.ref not in text
        if p.bank_code:
            assert f"/{p.bank_code.lower()}/" not in text
        assert "value date" not in text

    def test_note_appears_once(self):
        text = build_embed_text({"debit_credit": "debit",
                                 "raw_description": "UPI/Licious/121979067994/UPI Collect",
                                 "upi_meta": json.dumps({"note": "UPI Collect"})})
        assert text == "debit upi/licious/upi collect"


class TestResolver:
    def test_folds_truncation_variant(self):
        assert pick_canonical_key("OBEROIFC TUCKS", ["OBEROIFC TUCKSH"]) == "OBEROIFC TUCKSH"
        assert pick_canonical_key("OBEROIFC TUCKSH", ["OBEROIFC TUCKS"]) == "OBEROIFC TUCKS"

    def test_never_merges_distinct_people_sharing_a_prefix(self):
        # Same first 13 chars, different last letter: two people, not a truncation.
        assert pick_canonical_key("CHANDRA KUMAR P", ["CHANDRA KUMAR S"]) == "CHANDRA KUMAR P"

    def test_short_names_match_only_exactly(self):
        # Below the min length the bank prints names in full.
        assert pick_canonical_key("ASHOK", ["ASHOK KUMAR"]) == "ASHOK"
        assert pick_canonical_key("OBEROIFC", ["OBEROIFC SOUTH"]) == "OBEROIFC"

    def test_ambiguous_refuses_to_merge(self):
        assert pick_canonical_key("OBEROIFC TUCKS", ["OBEROIFC TUCKSH", "OBEROIFC TUCKSX"]) == "OBEROIFC TUCKS"

    def test_large_length_gap_is_not_truncation(self):
        assert pick_canonical_key("SMILE DENTIST", ["SMILE DENTIST CLINIC"]) == "SMILE DENTIST"

    def test_exact_hit_wins(self):
        assert pick_canonical_key("OBEROIFC TUCKS", ["OBEROIFC TUCKSH", "OBEROIFC TUCKS"]) == "OBEROIFC TUCKS"

    def test_db_resolver_uses_stored_keys(self):
        conn = _conn()
        _ingest(conn, "a", "UPI/OBEROIFC tucksh/118364694521/UPI")
        assert resolve_counterparty_key(conn, "OBEROIFC TUCKS") == "OBEROIFC TUCKSH"
        assert resolve_counterparty_key(conn, None) is None

    def test_in_memory_resolver_matches_db_resolver(self):
        r = KeyResolver()
        assert r.resolve("OBEROIFC TUCKSH") == "OBEROIFC TUCKSH"
        assert r.resolve("OBEROIFC TUCKS") == "OBEROIFC TUCKSH"
        assert r.resolve("CHANDRA KUMAR P") == "CHANDRA KUMAR P"
        assert r.resolve("CHANDRA KUMAR S") == "CHANDRA KUMAR S"
        assert r.resolve(None) is None

    def test_identity_prefers_stored_key_then_resolves(self):
        conn = _conn()
        _ingest(conn, "a", "UPI/OBEROIFC tucksh/118364694521/UPI")
        assert counterparty_identity(conn, {"counterparty_key": "STORED"}) == "STORED"
        eval_row = {"raw_description": "UPI/OBEROIFC tucks/YESB/125146338066/UPI"}
        assert counterparty_identity(conn, eval_row) == "OBEROIFC TUCKSH"

    def test_reversal_has_no_identity(self):
        # A refund credit must not feed the payee's learned rule / prior.
        assert normalize_identity("REV-UPI/Medkart Pharm/115697050961/Refund") is None


class TestLearnedRuleAcrossLayouts:
    def test_new_layout_row_hits_rule_taught_on_old_layout(self):
        from finance_copilot.db.queries.learned_rules import lookup_learned_rule

        conn = _conn()
        for i in range(3):
            _ingest(conn, f"old{i}", f"UPI/OBEROIFC tucksh/11836469452{i}/UPI", day=i + 1, month=5)
            insert_annotation(conn, Annotation(
                transaction_id=f"old{i}", category="Food & Dining", confidence=1.0, source="manual",
            ))
        _ingest(conn, "new", "UPI/OBEROIFC tucks/YESB/125146338066/UPI", day=22)
        conn.commit()
        row = dict(conn.execute("SELECT * FROM transactions WHERE id = 'new'").fetchone())
        rule = lookup_learned_rule(conn, counterparty_identity(conn, row))
        assert rule is not None
        assert rule.category == "Food & Dining"


class TestRederive:
    def _seed_stale(self, conn):
        # Rows as the previous parser stored them: raw (unresolved) keys and the
        # old upi_meta shape.
        conn.executemany(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, "
            "raw_description, upi_meta, counterparty_key) VALUES (?, 's1', ?, 1, 'debit', ?, ?, ?)",
            [
                ("a", "2026-05-01", "UPI/OBEROIFC tucksh/118364694521/UPI",
                 json.dumps({"vpa": None, "ref": "118364694521", "note": None}), "OBEROIFC TUCKSH"),
                ("b", "2026-06-20", "UPI/OBEROIFC tucks/YESB/125146338066/UPI",
                 json.dumps({"vpa": None, "ref": "125146338066", "note": None}), "OBEROIFC TUCKS"),
                ("c", "2026-06-21", "UPI/RAVI KUMAR SHA/123807871595/UPI (Value Date: 28-May-26)",
                 json.dumps({"vpa": None, "ref": "123807871595", "note": "UPI (Value Date: 28-May-26)"}),
                 "RAVI KUMAR SHA"),
                ("d", "2026-06-22", "UPI/NETFLIX/AUTOPAY", json.dumps({"note": "Netflix plan"}), "NETFLIX"),
            ],
        )
        conn.execute("INSERT INTO learned_rule_suppressions (counterparty_key) VALUES ('OBEROIFC TUCKS')")
        conn.commit()

    def test_keys_resolved_in_date_order(self):
        conn = _conn()
        self._seed_stale(conn)
        result = rederive_descriptions(conn)
        conn.commit()
        keys = dict(conn.execute("SELECT id, counterparty_key FROM transactions").fetchall())
        assert keys["a"] == keys["b"] == "OBEROIFC TUCKSH"
        assert result["keys_moved"] == 1

    def test_upi_meta_refreshed_for_known_shapes_only(self):
        conn = _conn()
        self._seed_stale(conn)
        rederive_descriptions(conn)
        meta = {r[0]: json.loads(r[1]) for r in conn.execute("SELECT id, upi_meta FROM transactions")}
        assert meta["b"]["bank"] == "YESB"
        assert meta["c"]["note"] is None  # value-date trailer no longer read as a note
        assert meta["d"]["note"] == "Netflix plan"  # unrecognized layout keeps the stored note

    def test_suppression_follows_moved_key(self):
        conn = _conn()
        self._seed_stale(conn)
        rederive_descriptions(conn)
        keys = [r[0] for r in conn.execute("SELECT counterparty_key FROM learned_rule_suppressions")]
        assert keys == ["OBEROIFC TUCKSH"]

    def test_reconcile_runs_once_per_version(self):
        conn = _conn()
        self._seed_stale(conn)
        reconcile_derived_data(conn)
        stored = conn.execute("SELECT value FROM app_settings WHERE key = ?", (PARSER_VERSION_KEY,)).fetchone()
        assert int(stored[0]) == DESCRIPTION_PARSER_VERSION
        # A second run with the version current must not touch rows.
        conn.execute("UPDATE transactions SET counterparty_key = 'SENTINEL' WHERE id = 'd'")
        conn.commit()
        reconcile_derived_data(conn)
        assert conn.execute("SELECT counterparty_key FROM transactions WHERE id = 'd'").fetchone()[0] == "SENTINEL"

    def test_embeddings_stale_detects_old_stamp(self):
        conn = _conn()
        self._seed_stale(conn)
        assert embeddings_stale(conn) is False  # no vectors at all: nothing to rebuild
        conn.execute("INSERT INTO embedding_meta (id, transaction_id, model_version) VALUES ('e1', 'a', 'nomic-embed-text')")
        assert embeddings_stale(conn) is True


class TestFormatWarning:
    def test_unrecognized_upi_lines_are_reported(self):
        from finance_copilot.models.transaction import Statement
        from finance_copilot.pipeline.ingest import check_description_formats

        conn = _conn()
        _ingest(conn, "a", "UPI/OBEROIFC tucks/YESB/125146338066/UPI")
        _ingest(conn, "b", "UPI/SOME SHOP/XY12/QQ/123456789012/UPI")
        stmt = Statement(id="s1", bank_name="test", parser_version="1", statement_month="2026-06")
        warnings = check_description_formats(conn, stmt)
        assert len(warnings) == 1
        assert warnings[0].startswith("1 UPI transaction is")

    def test_known_layouts_produce_no_warning(self):
        from finance_copilot.models.transaction import Statement
        from finance_copilot.pipeline.ingest import check_description_formats

        conn = _conn()
        _ingest(conn, "a", "UPI/OBEROIFC tucks/YESB/125146338066/UPI")
        stmt = Statement(id="s1", bank_name="test", parser_version="1", statement_month="2026-06")
        assert check_description_formats(conn, stmt) == []
