"""Auto-annotation pipeline: rules → RAG direct → RAG prompted → plain LLM."""
from __future__ import annotations

import difflib
import json
import logging
import sqlite3

logger = logging.getLogger(__name__)

from src.config import settings
from src.db.queries.annotations import insert_annotation
from src.db.queries.categories import get_category_names_flat
from src.db.queries.embeddings import find_similar
from src.db.queries.people import list_people
from src.db.queries.transactions import list_transactions
from src.models.annotation import Annotation, AnnotationCreate, AutoAnnotateResult
from src.pipeline.calibration import get_calibrated_dampening
from src.pipeline.embed import build_embed_text, get_embedding_single
from src.pipeline.llm import (
    annotate_transaction_llm,
    annotate_transaction_llm_with_examples,
    top_level_categories,
)
from src.pipeline.rules import apply_rules


def _normalize_category(category: str, category_list: list[str]) -> str:
    """Validate an LLM-returned category against the taxonomy.

    The JSON-schema enum should already constrain it, but small models can still
    slip (and older Ollama versions ignore enums), so: exact match → fuzzy match
    → 'Uncategorized'.
    """
    valid = top_level_categories(category_list)
    if not valid or category in valid:
        return category

    lower = category.strip().lower()
    for v in valid:
        if v.lower() == lower:
            return v

    # Unambiguous substring (e.g. 'Food' → 'Food & Dining')
    containing = [v for v in valid if lower in v.lower() or v.lower() in lower]
    if len(containing) == 1:
        logger.warning("category normalized | %r → %r", category, containing[0])
        return containing[0]

    close = difflib.get_close_matches(category, valid, n=1, cutoff=0.6)
    if close:
        logger.warning("category normalized | %r → %r", category, close[0])
        return close[0]
    logger.warning("category invalid | %r → 'Uncategorized'", category)
    return "Uncategorized"


def _match_known_person(txn: dict, known_people: list[tuple[str, str]]) -> AnnotationCreate | None:
    """Return a Peer Transfer annotation if the transaction description matches a known person.

    known_people is a list of (display_name, match_token) where match_token is already lowercased.
    """
    desc = (txn.get("raw_description") or "").lower()
    for name, token in known_people:
        if token in desc:
            return AnnotationCreate(
                transaction_id=txn["id"],
                merchant=name,
                category="Transfers",
                subcategory="Peer Transfer",
                tags=["transfer", "peer"],
                confidence=0.95,
                source="rule",
            )
    return None


def auto_annotate(
    conn: sqlite3.Connection,
    statement_id: str | None = None,
    transaction_ids: list[str] | None = None,
) -> AutoAnnotateResult:
    """Run the full auto-annotation pipeline on unannotated transactions.

    Stage 1 — rules:        keyword/merchant match → source=rule
    Stage 2 — rag_direct:   cosine similarity >= threshold → copy annotation → source=rag_direct
    Stage 3 — rag_prompted: cosine similarity found but below threshold → LLM with examples → source=rag_prompted
    Stage 4 — llm:          no similar found or embedding unavailable → plain LLM → source=llm
    """
    logger.info("auto_annotate start | statement_id=%s", statement_id)

    all_txns = list_transactions(conn, statement_id=statement_id)
    unannotated = list_transactions(conn, statement_id=statement_id, unannotated=True)
    if transaction_ids:
        id_set = set(transaction_ids)
        all_txns = [t for t in all_txns if t["id"] in id_set]
        unannotated = [t for t in unannotated if t["id"] in id_set]
    already_annotated_count = len(all_txns) - len(unannotated)

    logger.info(
        "txn counts | total=%d  unannotated=%d  already_annotated=%d",
        len(all_txns), len(unannotated), already_annotated_count,
    )

    rule_matched = 0
    rag_direct_annotated = 0
    rag_prompted_annotated = 0
    llm_annotated = 0
    llm_failed = 0
    low_confidence = 0

    # Commit in small batches: cheap enough to keep progress on a crash, without
    # paying a WAL sync per annotation.
    _COMMIT_EVERY = 10
    uncommitted = 0

    def _persist(ann_create: AnnotationCreate) -> None:
        nonlocal uncommitted
        annotation = Annotation(
            transaction_id=ann_create.transaction_id,
            merchant=ann_create.merchant,
            category=ann_create.category,
            subcategory=ann_create.subcategory,
            tags=",".join(ann_create.tags),
            confidence=ann_create.confidence,
            source=ann_create.source,
        )
        insert_annotation(conn, annotation)
        uncommitted += 1
        if uncommitted >= _COMMIT_EVERY:
            conn.commit()
            uncommitted = 0

    # Load known people once — used for peer transfer matching before merchant rules
    known_people = [(p["name"], p["upi"].lower()) for p in list_people(conn) if p.get("upi")]

    # --- Stage 1: Rule pass ---
    logger.info("stage 1 | rules | %d txns", len(unannotated))
    needs_rag: list[dict] = []
    for txn in unannotated:
        result = _match_known_person(txn, known_people) or apply_rules(txn)
        if result is not None:
            _persist(result)
            rule_matched += 1
            if result.confidence < settings.confidence_threshold:
                low_confidence += 1
            logger.debug(
                "rule match | txn=%s  desc=%r  → category=%s/%s  conf=%.2f",
                txn["id"], txn["raw_description"], result.category, result.subcategory, result.confidence,
            )
        else:
            needs_rag.append(txn)

    # --- Stages 2 + 3: RAG passes ---
    logger.info("stage 1 done | rule_matched=%d  needs_rag=%d", rule_matched, len(needs_rag))
    needs_llm: list[dict] = []
    category_list: list[str] = []
    if needs_rag:
        logger.info("stage 2+3 | rag | %d txns", len(needs_rag))
        category_list = get_category_names_flat(conn)
        for txn in needs_rag:
            rag_result = _try_rag_annotation(conn, txn, category_list)
            if rag_result is not None:
                _persist(rag_result)
                if rag_result.source == "rag_direct":
                    rag_direct_annotated += 1
                else:
                    rag_prompted_annotated += 1
                if rag_result.confidence < settings.confidence_threshold:
                    low_confidence += 1
            else:
                needs_llm.append(txn)

    # --- Stage 4: Plain LLM pass ---
    logger.info(
        "stage 2+3 done | rag_direct=%d  rag_prompted=%d  needs_llm=%d",
        rag_direct_annotated, rag_prompted_annotated, len(needs_llm),
    )
    if needs_llm:
        logger.info("stage 4 | llm | %d txns", len(needs_llm))
        if not category_list:
            category_list = get_category_names_flat(conn)
        for txn in needs_llm:
            logger.debug("llm | txn=%s  desc=%r  amount=%.2f", txn["id"], txn["raw_description"], txn["amount"])
            llm_result = annotate_transaction_llm(txn, category_list)
            if llm_result is not None:
                category = _normalize_category(llm_result.category, category_list)
                ann = AnnotationCreate(
                    transaction_id=txn["id"],
                    merchant=llm_result.merchant,
                    category=category,
                    subcategory=llm_result.subcategory,
                    tags=llm_result.tags,
                    confidence=round(llm_result.confidence * get_calibrated_dampening(conn, "llm", category), 4),
                    source="llm",
                )
                _persist(ann)
                llm_annotated += 1
                # Compare the stored (dampened) confidence — it decides review-queue membership
                if ann.confidence < settings.confidence_threshold:
                    low_confidence += 1
                logger.debug(
                    "llm result | txn=%s  → %s/%s  merchant=%r  raw_conf=%.2f  dampened_conf=%.4f",
                    txn["id"], llm_result.category, llm_result.subcategory, llm_result.merchant,
                    llm_result.confidence, ann.confidence,
                )
            else:
                llm_failed += 1
                logger.warning("llm failed | txn=%s  desc=%r", txn["id"], txn["raw_description"])

    conn.commit()  # flush the final partial batch
    logger.info(
        "auto_annotate done | total_processed=%d  rule=%d  rag_direct=%d  rag_prompted=%d  llm=%d  failed=%d  low_conf=%d  skipped=%d",
        len(unannotated), rule_matched, rag_direct_annotated, rag_prompted_annotated,
        llm_annotated, llm_failed, low_confidence, already_annotated_count,
    )

    return AutoAnnotateResult(
        total_processed=len(unannotated),
        rule_matched=rule_matched,
        rag_direct_annotated=rag_direct_annotated,
        rag_prompted_annotated=rag_prompted_annotated,
        llm_annotated=llm_annotated,
        llm_failed=llm_failed,
        low_confidence=low_confidence,
        already_annotated=already_annotated_count,
    )


def _compute_agreement_factor(annotated_matches: list[dict], top_category: str) -> float:
    """Discount factor based on how many top-K matches agree on the top category.

    Returns 1.0 when all matches agree (no penalty).
    Returns majority_fraction ** exponent when there's disagreement (gentle penalty).
    """
    if not annotated_matches:
        return 1.0
    majority_count = sum(1 for m in annotated_matches if m.get("category") == top_category)
    majority_fraction = majority_count / len(annotated_matches)
    if majority_fraction >= 1.0:
        return 1.0
    return majority_fraction ** settings.rag_agreement_exponent


def _compute_margin_factor(top_distance: float, next_diff_distance: float | None) -> float:
    """Discount factor based on the distance margin to the nearest different-category match.

    Returns 1.0 when the gap is large enough (clear winner) or no different category exists.
    Linearly interpolates from 0.85 (tied) to 1.0 (at rag_margin_safe distance apart).
    """
    if next_diff_distance is None:
        return 1.0
    margin = next_diff_distance - top_distance
    if margin >= settings.rag_margin_safe:
        return 1.0
    return 0.85 + 0.15 * (margin / settings.rag_margin_safe)


def _try_rag_annotation(
    conn: sqlite3.Connection,
    txn: dict,
    category_list: list[str],
) -> AnnotationCreate | None:
    """Attempt RAG-based annotation.

    Returns AnnotationCreate with source='rag_direct' or 'rag_prompted', or None if
    the embedding service is unavailable or no similar annotated transactions exist.
    """
    try:
        embed_text = build_embed_text(txn)
        query_vec = get_embedding_single(embed_text)
    except Exception as e:
        logger.warning("rag | embedding failed | txn=%s  error=%s", txn["id"], e)
        return None  # embedding service down — fall through to plain LLM

    similar = find_similar(
        conn,
        query_vec,
        top_k=settings.rag_top_k,
        exclude_transaction_ids=[txn["id"]],
    )

    if not similar:
        logger.debug("rag | no similar found | txn=%s  desc=%r", txn["id"], txn["raw_description"])
        return None

    # Novelty gate: if the best match is too far away, the examples are noise
    best_similarity = 1.0 - similar[0]["distance"]
    if best_similarity < settings.rag_similarity_floor:
        logger.debug(
            "rag | novelty gate | txn=%s  best_sim=%.4f < floor=%.4f → skip RAG",
            txn["id"], best_similarity, settings.rag_similarity_floor,
        )
        return None

    # Fetch annotations for all top-K matches upfront (used for agreement + margin analysis)
    top_match = similar[0]
    cosine_similarity = best_similarity
    ann_by_txn = _annotations_by_transaction(conn, [m["transaction_id"] for m in similar])
    annotated_matches = []
    for match in similar:
        ann = ann_by_txn.get(match["transaction_id"])
        if ann:
            annotated_matches.append({
                "transaction_id": match["transaction_id"],
                "distance": match["distance"],
                "category": ann.get("category"),
                "source": ann.get("source"),
                "annotation": ann,
            })

    logger.debug(
        "rag | top match | txn=%s  similar_txn=%s  cosine_sim=%.4f  threshold=%.4f  annotated_k=%d",
        txn["id"], top_match["transaction_id"], cosine_similarity, settings.rag_direct_threshold, len(annotated_matches),
    )

    # rag_direct: top match above similarity threshold → copy its annotation directly
    # Only trust human-verified or rule-matched annotations;
    # LLM/RAG-sourced donors fall through to rag_prompted so the LLM re-evaluates.
    _TRUSTED_SOURCES = {"manual", "rule", "imported"}
    if cosine_similarity >= settings.rag_direct_threshold:
        donor_ann = annotated_matches[0]["annotation"] if annotated_matches else None
        if donor_ann and donor_ann.get("source") in _TRUSTED_SOURCES:
            top_category = donor_ann["category"]

            # Agreement factor: penalize if top-K matches disagree on category
            agreement_factor = _compute_agreement_factor(annotated_matches, top_category)

            # Margin factor: penalize if nearest different-category match is close
            next_diff_distance = next(
                (m["distance"] for m in annotated_matches if m.get("category") != top_category),
                None,
            )
            margin_factor = _compute_margin_factor(top_match["distance"], next_diff_distance)

            confidence = round(cosine_similarity * agreement_factor * margin_factor, 4)
            logger.debug(
                "rag_direct | txn=%s  → %s/%s  cosine=%.4f  agreement=%.4f  margin=%.4f  conf=%.4f  donor_source=%s",
                txn["id"], top_category, donor_ann.get("subcategory"),
                cosine_similarity, agreement_factor, margin_factor, confidence, donor_ann["source"],
            )
            return AnnotationCreate(
                transaction_id=txn["id"],
                merchant=donor_ann.get("merchant"),
                category=top_category,
                subcategory=donor_ann.get("subcategory"),
                tags=[t for t in donor_ann.get("tags", "").split(",") if t],
                confidence=confidence,
                source="rag_direct",
            )
        elif donor_ann:
            logger.debug(
                "rag_direct skipped | txn=%s  donor_source=%s (untrusted) → falling through to rag_prompted",
                txn["id"], donor_ann.get("source"),
            )

    # rag_prompted: inject similar examples as few-shot context into the LLM prompt
    examples = _build_examples_from_similar(conn, similar, ann_by_txn)
    logger.debug("rag_prompted | txn=%s  examples=%d", txn["id"], len(examples))
    if examples:
        llm_result = annotate_transaction_llm_with_examples(txn, category_list, examples)
        if llm_result is not None:
            category = _normalize_category(llm_result.category, category_list)
            confidence = round(llm_result.confidence * get_calibrated_dampening(conn, "rag_prompted", category), 4)
            logger.debug(
                "rag_prompted result | txn=%s  → %s/%s  raw_conf=%.2f  dampened_conf=%.4f",
                txn["id"], category, llm_result.subcategory, llm_result.confidence, confidence,
            )
            return AnnotationCreate(
                transaction_id=txn["id"],
                merchant=llm_result.merchant,
                category=category,
                subcategory=llm_result.subcategory,
                tags=llm_result.tags,
                confidence=confidence,
                source="rag_prompted",
            )
        logger.warning("rag_prompted | llm returned nothing | txn=%s", txn["id"])

    return None


def _annotations_by_transaction(conn: sqlite3.Connection, transaction_ids: list[str]) -> dict[str, dict]:
    """Fetch annotations for many transactions in one query, keyed by transaction id."""
    if not transaction_ids:
        return {}
    placeholders = ",".join("?" * len(transaction_ids))
    rows = conn.execute(
        f"SELECT * FROM annotations WHERE transaction_id IN ({placeholders})",
        transaction_ids,
    ).fetchall()
    return {row["transaction_id"]: dict(row) for row in rows}


def _build_examples_from_similar(
    conn: sqlite3.Connection,
    similar: list[dict],
    ann_by_txn: dict[str, dict],
) -> list[dict]:
    """Fetch transaction details for similar matches to use as few-shot examples."""
    txn_ids = [m["transaction_id"] for m in similar]
    placeholders = ",".join("?" * len(txn_ids))
    txn_by_id = {
        row["id"]: row
        for row in conn.execute(
            f"SELECT * FROM transactions WHERE id IN ({placeholders})", txn_ids
        ).fetchall()
    } if txn_ids else {}

    examples = []
    for match in similar:
        txn_row = txn_by_id.get(match["transaction_id"])
        ann_row = ann_by_txn.get(match["transaction_id"])
        if txn_row is None or ann_row is None:
            continue
        upi_note = ""
        upi_meta = txn_row["upi_meta"] if "upi_meta" in txn_row.keys() else None
        if upi_meta:
            try:
                meta = json.loads(upi_meta) if isinstance(upi_meta, str) else upi_meta
                upi_note = str(meta.get("note", ""))
            except (json.JSONDecodeError, AttributeError):
                pass
        examples.append({
            "raw_description": txn_row["raw_description"],
            "upi_note": upi_note,
            "amount": txn_row["amount"],
            "debit_credit": txn_row["debit_credit"],
            "txn_date": txn_row["txn_date"],
            "category": ann_row["category"],
            "subcategory": ann_row.get("subcategory"),
            "merchant": ann_row.get("merchant"),
            "source": ann_row.get("source"),
        })

    # Prioritize human-verified examples first — LLMs are sensitive to example ordering
    _SOURCE_PRIORITY = {"manual": 0, "rule": 1, "imported": 2}
    examples.sort(key=lambda e: _SOURCE_PRIORITY.get(e.get("source", ""), 9))
    return examples
