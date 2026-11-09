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
from finance_copilot.parsers.upi import parse_upi_description
from finance_copilot.pipeline.counterparty import (
    canonical_keys,
    normalize_identity,
    resolve_counterparty_key,
    truncation_variants,
)
from finance_copilot.pipeline.derived import (
    PARSER_VERSION_KEY,
    reconcile_derived_data,
    rederive_descriptions,
)
from finance_copilot.pipeline.embed import build_embed_text, embed_text_hash, stale_embedding_rows

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
        debit_credit="debit", raw_description=desc, upi_meta=parse_upi_description(desc, NOISE),
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
        key = normalize_identity(case["raw"])
        text = build_embed_text({"debit_credit": "debit", "raw_description": case["raw"], "counterparty_key": key})
        assert p.ref not in text
        if p.bank_code:
            assert f"/{p.bank_code.lower()}/" not in text
        assert "value date" not in text

    def test_note_appears_once(self):
        text = build_embed_text({"debit_credit": "debit",
                                 "raw_description": "UPI/Licious/121979067994/UPI Collect",
                                 "upi_meta": json.dumps({"note": "UPI Collect"}),
                                 "counterparty_key": "LICIOUS"})
        assert text == "debit upi/licious/upi collect"


class TestResolver:
    @pytest.mark.parametrize(("a", "b"), [
        ("OBEROIFC TUCKS", "OBEROIFC TUCKSH"),     # 14 vs 15: bank prefix took a char
        ("AGODA COMPANY", "AGODA COMPANY P"),      # the cut landed on a space
        ("ROPPEN TRANSPO", "ROPPEN TRANSPOR"),
    ])
    def test_truncation_variants(self, a, b):
        assert truncation_variants(a, b) and truncation_variants(b, a)

    @pytest.mark.parametrize(("a", "b"), [
        ("MOHAMMED ALI", "MOHAMMED ALI KH"),       # a full 12-char name is not a cut
        ("VIKRAM SINGH P", "VIKRAM SINGH S"),    # same length, different people
        ("SMILE DENTIST", "SMILE DENTIST CLINIC"), # far longer: a different name
        ("MEDKART PHARM", "MEDKART PHARMAC"),      # two chars, no space at the cut
        ("ASHOK", "ASHOK KUMAR"),
    ])
    def test_not_truncation_variants(self, a, b):
        assert not truncation_variants(a, b)

    def test_canonical_is_the_longest_spelling_in_any_order(self):
        keys = ["OBEROIFC TUCKS", "OBEROIFC TUCKSH", "MOHAMMED ALI", "MOHAMMED ALI KH", "MOHAMMED ALIYA"]
        for order in (keys, list(reversed(keys))):
            got = canonical_keys(order)
            assert got["OBEROIFC TUCKS"] == got["OBEROIFC TUCKSH"] == "OBEROIFC TUCKSH"
            assert got["MOHAMMED ALI"] == "MOHAMMED ALI"
            assert got["MOHAMMED ALI KH"] == "MOHAMMED ALI KH"
            assert got["MOHAMMED ALIYA"] == "MOHAMMED ALIYA"

    def test_ambiguous_refuses_to_merge(self):
        got = canonical_keys(["OBEROIFC TUCKS", "OBEROIFC TUCKSH", "OBEROIFC TUCKSX"])
        assert got == {k: k for k in ("OBEROIFC TUCKS", "OBEROIFC TUCKSH", "OBEROIFC TUCKSX")}

    def test_ingest_order_reaches_the_same_answer(self):
        # Newer statement (short spelling) uploaded first, then the older one.
        conn = _conn()
        _ingest(conn, "new", "UPI/OBEROIFC tucks/YESB/125146338066/UPI", day=20)
        conn.execute("INSERT INTO learned_rule_suppressions (counterparty_key) VALUES ('OBEROIFC TUCKS')")
        _ingest(conn, "old", "UPI/OBEROIFC tucksh/118364694521/UPI", day=1)
        keys = {r[0] for r in conn.execute("SELECT counterparty_key FROM transactions")}
        assert keys == {"OBEROIFC TUCKSH"}
        assert [r[0] for r in conn.execute("SELECT counterparty_key FROM learned_rule_suppressions")] == [
            "OBEROIFC TUCKSH"
        ]

    def test_short_full_names_never_absorb_longer_ones(self):
        conn = _conn()
        _ingest(conn, "a", "UPI/MOHAMMED ALI/118364694521/UPI")
        assert resolve_counterparty_key(conn, "MOHAMMED ALI KH") == "MOHAMMED ALI KH"

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
        rule = lookup_learned_rule(conn, row["counterparty_key"])
        assert rule is not None
        assert rule.category == "Food & Dining"


class TestRederive:
    def _seed_stale(self, conn):
        # Rows as an older parser stored them: raw (unresolved) keys and the old
        # upi_meta shape.
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
            ],
        )
        conn.execute("INSERT INTO learned_rule_suppressions (counterparty_key) VALUES ('OBEROIFC TUCKS')")
        conn.commit()

    def test_keys_resolved_to_longest_spelling(self):
        conn = _conn()
        self._seed_stale(conn)
        result = rederive_descriptions(conn)
        keys = dict(conn.execute("SELECT id, counterparty_key FROM transactions").fetchall())
        assert keys["a"] == keys["b"] == "OBEROIFC TUCKSH"
        assert result["keys_moved"] == 1

    def test_rederive_equals_reingest(self):
        conn = _conn()
        self._seed_stale(conn)
        rederive_descriptions(conn)
        for r in conn.execute("SELECT raw_description, upi_meta FROM transactions"):
            assert r["upi_meta"] == parse_upi_description(r["raw_description"], NOISE)

    def test_suppression_follows_moved_key(self):
        conn = _conn()
        self._seed_stale(conn)
        rederive_descriptions(conn)
        keys = [r[0] for r in conn.execute("SELECT counterparty_key FROM learned_rule_suppressions")]
        assert keys == ["OBEROIFC TUCKSH"]

    def test_suppression_stays_when_a_key_splits(self):
        conn = _conn()
        conn.executemany(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, "
            "counterparty_key) VALUES (?, 's1', '2026-06-01', 1, 'debit', ?, 'MERGED')",
            [("x", "UPI/FIRST PAYEE/118364694521/UPI"), ("y", "UPI/SECOND PAYEE/118364694522/UPI")],
        )
        conn.execute("INSERT INTO learned_rule_suppressions (counterparty_key) VALUES ('MERGED')")
        rederive_descriptions(conn)
        assert [r[0] for r in conn.execute("SELECT counterparty_key FROM learned_rule_suppressions")] == ["MERGED"]

    def test_reconcile_runs_once_per_version(self):
        conn = _conn()
        self._seed_stale(conn)
        reconcile_derived_data(conn)
        stored = conn.execute("SELECT value FROM app_settings WHERE key = ?", (PARSER_VERSION_KEY,)).fetchone()
        assert int(stored[0]) == DESCRIPTION_PARSER_VERSION
        # A second run with the version current must not touch rows.
        conn.execute("UPDATE transactions SET counterparty_key = 'SENTINEL' WHERE id = 'c'")
        conn.commit()
        reconcile_derived_data(conn)
        assert conn.execute("SELECT counterparty_key FROM transactions WHERE id = 'c'").fetchone()[0] == "SENTINEL"


class TestStaleEmbeddings:
    def _annotated(self, conn, txn_id, desc):
        _ingest(conn, txn_id, desc)
        insert_annotation(conn, Annotation(transaction_id=txn_id, category="Food & Dining",
                                           confidence=1.0, source="manual"))
        conn.commit()

    def _meta(self, conn, txn_id, model, text_hash):
        conn.execute("INSERT INTO embedding_meta (id, transaction_id, model_version, text_hash) VALUES (?, ?, ?, ?)",
                     (f"e_{txn_id}", txn_id, model, text_hash))

    def test_missing_other_model_and_changed_text_are_stale(self):
        from finance_copilot.config import current_embedding_model

        conn = _conn()
        for t in ("missing", "other_model", "changed", "current"):
            self._annotated(conn, t, f"UPI/PAYEE {t.upper()}/11836469{len(t):04d}/UPI")
        texts = {r["id"]: build_embed_text(dict(r)) for r in conn.execute("SELECT * FROM transactions")}
        self._meta(conn, "other_model", "some-other-model", embed_text_hash(texts["other_model"]))
        self._meta(conn, "changed", current_embedding_model(), embed_text_hash("an older text"))
        self._meta(conn, "current", current_embedding_model(), embed_text_hash(texts["current"]))
        stale = {txn["id"] for txn, _text in stale_embedding_rows(conn)}
        assert stale == {"missing", "other_model", "changed"}


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


class TestBalanceLabelLeak:
    """The statement's balance label must not end up in a transaction description."""

    @pytest.mark.parametrize(("raw", "clean"), [
        ("UPI/ZOMATO/122406278840/UPI Closing Balance", "UPI/ZOMATO/122406278840/UPI"),
        ("UPI/RAVI KUMAR SHA/124007205535/self\nClosing Balance", "UPI/RAVI KUMAR SHA/124007205535/self"),
        ("Int.Pd:3250508074:01-01-2026 to 31-03-2026 Closing Balance",
         "Int.Pd:3250508074:01-01-2026 to 31-03-2026"),
        ("UPI/SWIGGY/122406278841/UPI Opening Balance", "UPI/SWIGGY/122406278841/UPI"),
        # A standalone balance row is ingest's balance artifact, left untouched.
        ("Closing Balance", "Closing Balance"),
    ])
    def test_kotak_parser_strips_trailing_label(self, raw, clean):
        from finance_copilot.parsers.banks.kotak import KotakParser

        assert KotakParser()._clean_description(raw) == clean

    def test_migration_repairs_stored_rows(self):
        conn = _conn()
        conn.executemany(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
            "VALUES (?, 's1', '2026-05-31', 1, 'debit', ?)",
            [("a", "UPI/ZOMATO/122406278840/UPI Closing Balance"),
             ("b", "Closing Balance"),
             ("c", "UPI/SWIGGY/122406278841/UPI")],
        )
        sql = (Path(__file__).parents[1] / "db" / "migrations"
               / "027_strip_balance_label_from_descriptions.sql").read_text()
        conn.executescript(sql)
        got = dict(conn.execute("SELECT id, raw_description FROM transactions").fetchall())
        assert got == {"a": "UPI/ZOMATO/122406278840/UPI", "b": "Closing Balance",
                       "c": "UPI/SWIGGY/122406278841/UPI"}
