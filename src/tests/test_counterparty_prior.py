"""Tests for the counterparty recurrence prior (empirical-Bayes, cold-start-safe)."""
from __future__ import annotations

import sqlite3

import pytest

from src.config import settings
from src.db.connection import init_db
from src.db.queries.annotations import insert_annotation
from src.models.annotation import Annotation
from src.pipeline.counterparty import (
    CounterpartyPrior,
    counterparty_history,
    counterparty_prior,
    normalize_identity,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    conn.execute(
        "INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) "
        "VALUES ('stmt_01', 'test', '1', '2026-01')"
    )
    conn.commit()
    return conn


def _insert(conn, txn_id, description, category, *, source="manual",
            txn_date="2026-01-15") -> None:
    """Insert a transaction + its annotation as a prior data point."""
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
        "VALUES (?, 'stmt_01', ?, 100.0, 'debit', ?)",
        (txn_id, txn_date, description),
    )
    insert_annotation(conn, Annotation(
        transaction_id=txn_id, category=category, confidence=0.95, source=source,
    ))
    conn.commit()


# ---------------------------------------------------------------------------
# normalize_identity
# ---------------------------------------------------------------------------

class TestNormalizeIdentity:
    def test_basic_name_segment(self):
        assert normalize_identity("UPI/ANSHU YADAV/121036708976/UPI") == "ANSHU YADAV"

    def test_case_and_whitespace_normalized(self):
        # Trailing space + lowercase collapse to the same key (the 239→231 merge).
        assert normalize_identity("UPI/Anshu  Yadav /1/UPI") == "ANSHU YADAV"

    def test_consistent_truncation_collides_with_itself(self):
        a = normalize_identity("UPI/ANANTA KUMAR BO/606625794684/UPI")
        b = normalize_identity("UPI/ANANTA KUMAR BO/608874544144/UPI")
        assert a == b == "ANANTA KUMAR BO"

    def test_non_upi_returns_none(self):
        assert normalize_identity("PCD/1280/SPOTIFY/2240/16:44") is None
        assert normalize_identity("SAL CREDIT ACME CORP") is None

    def test_empty_or_none(self):
        assert normalize_identity(None) is None
        assert normalize_identity("") is None
        assert normalize_identity("UPI//1/UPI") is None  # empty name segment


# ---------------------------------------------------------------------------
# counterparty_history — causality / leakage
# ---------------------------------------------------------------------------

class TestCounterpartyHistory:
    def setup_method(self):
        self.conn = _make_conn()

    def teardown_method(self):
        self.conn.close()

    def test_groups_same_counterparty_across_descriptions(self):
        _insert(self.conn, "h1", "UPI/SANYA PRASHANT /603975177215/UPI", "Transfers")
        _insert(self.conn, "h2", "UPI/SANYA PRASHANT /605037951331/UPI", "Transfers")
        hist = counterparty_history(self.conn, "SANYA PRASHANT")
        assert len(hist) == 2

    def test_before_date_excludes_future_labels(self):
        _insert(self.conn, "past", "UPI/RAM/1/UPI", "Transport", txn_date="2026-01-01")
        _insert(self.conn, "future", "UPI/RAM/2/UPI", "Transport", txn_date="2026-03-01")
        # Scoring a txn dated Feb only sees the January label, not March.
        hist = counterparty_history(self.conn, "RAM", before_txn_date="2026-02-01")
        assert [h["transaction_id"] for h in hist] == ["past"]

    def test_excludes_self(self):
        _insert(self.conn, "self_txn", "UPI/RAM/1/UPI", "Transport")
        hist = counterparty_history(self.conn, "RAM", exclude_transaction_id="self_txn")
        assert hist == []


# ---------------------------------------------------------------------------
# counterparty_prior — empirical-Bayes shrinkage
# ---------------------------------------------------------------------------

class TestCounterpartyPrior:
    def setup_method(self):
        self.conn = _make_conn()

    def teardown_method(self):
        self.conn.close()

    def test_unknown_counterparty_is_inert(self):
        """New user / never-seen counterparty → no prior, no nudge (cold start)."""
        txn = {"id": "t", "raw_description": "UPI/BRAND NEW PERSON/9/UPI",
               "txn_date": "2026-02-01"}
        prior = counterparty_prior(self.conn, txn)
        assert prior == CounterpartyPrior()
        assert prior.category is None
        assert not prior.established

    def test_non_upi_is_inert(self):
        txn = {"id": "t", "raw_description": "NEFT SALARY CREDIT", "txn_date": "2026-02-01"}
        assert not counterparty_prior(self.conn, txn).established

    def test_single_observation_not_established(self):
        """One prior label is not enough to clear min_observations → still inert.

        This is the day-1-skew guard: the first payment to anyone can't yet drive
        routing, even though a category technically 'wins'.
        """
        _insert(self.conn, "p1", "UPI/KARABI BORA/1/UPI", "Transfers", txn_date="2026-01-01")
        txn = {"id": "t", "raw_description": "UPI/KARABI BORA/2/UPI", "txn_date": "2026-02-01"}
        prior = counterparty_prior(self.conn, txn)
        assert prior.n_prior == 1
        assert prior.category == "Transfers"
        assert not prior.established  # below counterparty_min_observations (3)

    def test_recurring_consistent_counterparty_is_established(self):
        """A counterparty seen several times, consistently labeled, clears the bar."""
        for i in range(4):
            _insert(self.conn, f"k{i}", f"UPI/KARABI BORA/{i}/UPI", "Transfers",
                    txn_date=f"2026-01-0{i+1}")
        txn = {"id": "t", "raw_description": "UPI/KARABI BORA/9/UPI", "txn_date": "2026-02-01"}
        prior = counterparty_prior(self.conn, txn)
        assert prior.n_prior == 4
        assert prior.category == "Transfers"
        assert prior.established
        assert prior.probability >= settings.counterparty_dominance_floor

    def test_machine_labels_downweighted(self):
        """rag_prompted/llm priors carry reduced weight vs trusted sources."""
        for i in range(4):
            _insert(self.conn, f"m{i}", f"UPI/SOMEONE/{i}/UPI", "Transport",
                    source="rag_prompted", txn_date=f"2026-01-0{i+1}")
        txn = {"id": "t", "raw_description": "UPI/SOMEONE/9/UPI", "txn_date": "2026-02-01"}
        prior = counterparty_prior(self.conn, txn)
        assert prior.n_prior == 4
        # 4 machine labels at rag_machine_donor_weight each.
        expected = round(4 * settings.rag_machine_donor_weight, 4)
        assert prior.trusted_weight == expected

    def test_mixed_categories_winner_and_shrinkage(self):
        """Split labels lower the dominant probability via shrinkage."""
        for i in range(3):
            _insert(self.conn, f"a{i}", f"UPI/MIXED/{i}/UPI", "Transport",
                    txn_date=f"2026-01-0{i+1}")
        _insert(self.conn, "b0", "UPI/MIXED/8/UPI", "Transfers", txn_date="2026-01-08")
        txn = {"id": "t", "raw_description": "UPI/MIXED/9/UPI", "txn_date": "2026-02-01"}
        prior = counterparty_prior(self.conn, txn)
        assert prior.category == "Transport"  # 3 vs 1
        # base = 1/2 categories = 0.5, m=2, winner=3, total=4 → (2*0.5+3)/(2+4)=0.6667
        assert prior.probability == pytest.approx((2 * 0.5 + 3) / (2 + 4), abs=1e-3)

    def test_prior_uses_txn_date_for_causality_by_default(self):
        """Without an explicit before_date, the txn's own date bounds the history."""
        _insert(self.conn, "old", "UPI/RECUR/1/UPI", "Transport", txn_date="2026-01-01")
        _insert(self.conn, "newer", "UPI/RECUR/2/UPI", "Transport", txn_date="2026-05-01")
        # Scoring a txn dated Feb must not see the May label.
        txn = {"id": "t", "raw_description": "UPI/RECUR/9/UPI", "txn_date": "2026-02-01"}
        prior = counterparty_prior(self.conn, txn)
        assert prior.n_prior == 1


# ---------------------------------------------------------------------------
# _fuse_counterparty_prior — the late-fusion decision logic
# ---------------------------------------------------------------------------

class TestFuseCounterpartyPrior:
    def _established(self, category="Transfers", probability=0.9, n=4) -> CounterpartyPrior:
        return CounterpartyPrior(
            n_prior=n, trusted_weight=float(n), category=category,
            probability=probability, histogram={category: float(n)},
        )

    def test_neutral_when_prior_not_established(self):
        from src.pipeline.annotate import _fuse_counterparty_prior
        conf, effect = _fuse_counterparty_prior(0.17, "Transfers", CounterpartyPrior())
        assert effect == "neutral"
        assert conf == 0.17  # unchanged → cold-start no-op

    def test_neutral_when_disabled(self, monkeypatch):
        from src.pipeline.annotate import _fuse_counterparty_prior
        monkeypatch.setattr(settings, "counterparty_prior_enabled", False)
        conf, effect = _fuse_counterparty_prior(0.17, "Transfers", self._established())
        assert effect == "neutral"
        assert conf == 0.17

    def test_rescue_lifts_dampened_agreeing_confidence(self):
        """The KARABI BORA case: calibration crushed Transfers to 0.17, but an
        established Transfers prior rescues it toward the prior probability."""
        from src.pipeline.annotate import _fuse_counterparty_prior
        conf, effect = _fuse_counterparty_prior(
            0.17, "Transfers", self._established(category="Transfers", probability=0.9)
        )
        assert effect == "rescue"
        assert conf == 0.9
        assert conf > settings.confidence_threshold  # now clears review

    def test_rescue_does_not_lower_already_confident(self):
        from src.pipeline.annotate import _fuse_counterparty_prior
        conf, effect = _fuse_counterparty_prior(
            0.95, "Transfers", self._established(category="Transfers", probability=0.8)
        )
        # max(0.95, 0.8) = 0.95 → no change → neutral, never reduces on agreement
        assert effect == "neutral"
        assert conf == 0.95

    def test_tighten_caps_on_disagreement(self):
        """Established prior says Transfers but LLM picked Transport → cap to review.
        Covers both the cab-misfire and a recurring contact's off-category spend."""
        from src.pipeline.annotate import _fuse_counterparty_prior
        conf, effect = _fuse_counterparty_prior(
            0.83, "Transport", self._established(category="Transfers", probability=0.9)
        )
        assert effect == "tighten"
        assert conf <= settings.rag_defer_confidence_cap
        assert conf < settings.confidence_threshold

    def test_tighten_noop_when_already_below_cap(self):
        from src.pipeline.annotate import _fuse_counterparty_prior
        conf, effect = _fuse_counterparty_prior(
            0.2, "Transport", self._established(category="Transfers")
        )
        # already <= defer cap → no further change → neutral
        assert effect == "neutral"
        assert conf == 0.2


# ---------------------------------------------------------------------------
# End-to-end: prior fused through the real rag_prompted pipeline
# ---------------------------------------------------------------------------

class TestCounterpartyPriorInPipeline:
    def setup_method(self):
        self.conn = _make_conn()

    def teardown_method(self):
        self.conn.close()

    def _insert_donor(self, txn_id, description, category, subcategory=None,
                      source="manual", txn_date="2026-01-15"):
        self.conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
            "VALUES (?, 'stmt_01', ?, 100.0, 'debit', ?)",
            (txn_id, txn_date, description),
        )
        insert_annotation(self.conn, Annotation(
            transaction_id=txn_id, category=category, subcategory=subcategory,
            confidence=0.95, source=source,
        ))
        self.conn.commit()

    def test_rescue_through_pipeline(self):
        """A recurring KARABI BORA (4 prior Transfers) rescues a dampened
        rag_prompted Transfers prediction past the review threshold."""
        from unittest.mock import patch
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse
        from src.db.queries.annotations import get_annotation_by_transaction

        # 4 prior trusted Transfers labels for KARABI BORA (establishes the prior).
        for i in range(4):
            self._insert_donor(f"kb{i}", f"UPI/KARABI BORA/{i}/UPI", "Transfers",
                               "Family", txn_date=f"2026-01-0{i+1}")
        # Target: another KARABI BORA txn, dated after the history.
        self.conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
            "VALUES ('target_kb', 'stmt_01', '2026-02-01', 100.0, 'debit', 'UPI/KARABI BORA/9/UPI')"
        )
        self.conn.commit()

        # LLM picks Transfers; calibration would dampen it, but the prior agrees.
        llm_result = AnnotationResponse(category="Transfers", subcategory="Family", confidence=0.85)
        mock_vec = [0.1] * 768
        similar = [{"transaction_id": f"kb{i}", "distance": 0.20 + i * 0.01} for i in range(4)]
        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=similar), \
             patch("src.pipeline.annotate.annotate_transaction_llm_with_examples", return_value=llm_result):
            auto_annotate(self.conn, transaction_ids=["target_kb"])

        ann = get_annotation_by_transaction(self.conn, "target_kb")
        assert ann is not None
        assert ann["category"] == "Transfers"
        # Rescued toward the prior probability → clears the review threshold.
        assert ann["confidence"] > settings.confidence_threshold

    def test_cold_start_unaffected_through_pipeline(self):
        """A first-time counterparty gets no prior nudge — behaves exactly as before."""
        from unittest.mock import patch
        from src.pipeline.annotate import auto_annotate
        from src.pipeline.llm import AnnotationResponse
        from src.db.queries.annotations import get_annotation_by_transaction

        self._insert_donor("d_shop", "UPI/SOME SHOP/1/UPI", "Shopping")
        self.conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
            "VALUES ('target_new', 'stmt_01', '2026-02-01', 100.0, 'debit', 'UPI/NEVER SEEN/9/UPI')"
        )
        self.conn.commit()

        llm_result = AnnotationResponse(category="Shopping", confidence=0.9)
        mock_vec = [0.1] * 768
        with patch("src.pipeline.annotate.get_embedding_single", return_value=mock_vec), \
             patch("src.pipeline.annotate.find_similar", return_value=[{"transaction_id": "d_shop", "distance": 0.2}]), \
             patch("src.pipeline.annotate.annotate_transaction_llm_with_examples", return_value=llm_result):
            auto_annotate(self.conn, transaction_ids=["target_new"])

        ann = get_annotation_by_transaction(self.conn, "target_new")
        # 0.9 * rag dampen (0.92) = 0.828, untouched by any prior.
        assert abs(ann["confidence"] - round(0.9 * 0.92, 4)) < 1e-4
