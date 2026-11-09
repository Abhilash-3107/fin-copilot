"""RAG evidence set in the LLM path, per-direction dismissals, and bank-line
merchants (findings/2026-11-09-pipeline-robustness/02)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from finance_copilot.db.connection import init_db
from finance_copilot.db.queries.learned_rules import list_learned_rules, suppress_learned_rule
from finance_copilot.pipeline.llm import AnnotationResponse

MIGRATIONS = Path(__file__).parents[1] / "db" / "migrations"


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


def _labeled(conn, txn_id, name, category, *, dc="debit", day=1, source="manual"):
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
        "VALUES (?, 's', ?, 100, ?, ?, ?)",
        (txn_id, f"2026-06-{day:02d}", dc, f"UPI/{name}/1234567{day:05d}/UPI", name),
    )
    conn.execute(
        "INSERT INTO annotations (id, transaction_id, category, confidence, source) VALUES (?, ?, ?, 1.0, ?)",
        (f"a_{txn_id}", txn_id, category, source),
    )
    conn.commit()


class TestPromptedPathUsesEvidence:
    def test_below_floor_neighbours_are_not_shown_to_the_llm(self):
        from finance_copilot.pipeline.annotate import _try_rag_annotation

        conn = _conn()
        _labeled(conn, "near", "GALAXY CINEMAS", "Entertainment")
        _labeled(conn, "far1", "SOME SHOP", "Shopping", day=2)
        _labeled(conn, "far2", "OTHER SHOP", "Shopping", day=3)
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
            "VALUES ('t', 's', '2026-06-20', 300, 'debit', 'UPI/GALAXY CINEMA/123456789012/UPI', 'GALAXY CINEMA')"
        )
        txn = dict(conn.execute("SELECT * FROM transactions WHERE id = 't'").fetchone())
        similar = [
            {"transaction_id": "near", "distance": 0.2},   # 0.80: evidence, below rag_direct
            {"transaction_id": "far1", "distance": 0.6},   # 0.40: noise
            {"transaction_id": "far2", "distance": 0.65},
        ]
        seen = {}

        def llm(_txn, _cats, examples, majority, count):
            seen.update(examples=[e["category"] for e in examples], majority=majority, count=count)
            return AnnotationResponse(category="Entertainment", confidence=0.9)

        with patch("finance_copilot.pipeline.annotate.get_embedding_single", return_value=[0.1] * 768), \
             patch("finance_copilot.pipeline.annotate.find_similar", return_value=similar), \
             patch("finance_copilot.pipeline.annotate.annotate_transaction_llm_with_examples", side_effect=llm):
            ann, trace = _try_rag_annotation(conn, txn, ["Entertainment", "Shopping"])

        assert seen == {"examples": ["Entertainment"], "majority": "Entertainment", "count": 1}
        assert trace.evidence_count == 1
        assert "off_example" not in trace.caps_applied


class TestDismissalPerDirection:
    def test_dismissing_the_refund_rule_keeps_the_payment_rule(self):
        conn = _conn()
        for i in range(3):
            _labeled(conn, f"p{i}", "PRACTO TECHNOLO", "Health", day=i + 1)
            _labeled(conn, f"r{i}", "PRACTO TECHNOLO", "Income", dc="credit", day=i + 10)
        assert {r.direction for r in list_learned_rules(conn)} == {"debit", "credit"}
        suppress_learned_rule(conn, "PRACTO TECHNOLO", "credit")
        assert [(r.counterparty_key, r.direction) for r in list_learned_rules(conn)] == [("PRACTO TECHNOLO", "debit")]

    def test_migration_carries_old_dismissals_to_both_directions(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE learned_rule_suppressions (counterparty_key TEXT PRIMARY KEY, "
                     "dismissed_at TIMESTAMP DEFAULT (datetime('now')))")
        conn.execute("INSERT INTO learned_rule_suppressions (counterparty_key) VALUES ('SWIGGY')")
        conn.executescript((MIGRATIONS / "029_suppressions_per_direction.sql").read_text())
        rows = sorted(conn.execute("SELECT counterparty_key, direction FROM learned_rule_suppressions"))
        assert rows == [("SWIGGY", "credit"), ("SWIGGY", "debit")]


class TestBankLineMerchants:
    def test_llm_echo_of_the_description_is_not_a_merchant(self):
        echoed = AnnotationResponse(category="Self Transfers", confidence=0.9,
                                    merchant="UPI/RAVI KUMAR SHA/120777386275/self")
        assert echoed.merchant is None
        assert AnnotationResponse(category="Shopping", confidence=0.9, merchant="H&M / Zara").merchant == "H&M / Zara"

    def test_migration_clears_stored_bank_line_merchants(self):
        conn = _conn()
        _labeled(conn, "x", "RAVI KUMAR SHA", "Self Transfers")
        conn.execute("UPDATE annotations SET merchant = 'UPI/RAVI KUMAR SHA/120777386275/self'")
        _labeled(conn, "y", "SWIGGY", "Food & Dining", day=2)
        conn.execute("UPDATE annotations SET merchant = 'Swiggy' WHERE transaction_id = 'y'")
        conn.executescript((MIGRATIONS / "030_clear_bank_line_merchants.sql").read_text())
        assert dict(conn.execute("SELECT transaction_id, merchant FROM annotations").fetchall()) == {
            "x": None, "y": "Swiggy",
        }
