"""Auto-annotation pipeline: rules → RAG direct → RAG prompted → plain LLM."""
from __future__ import annotations

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)

from src.config import settings
from src.db.queries.annotations import get_annotation_by_transaction, insert_annotation
from src.db.queries.categories import get_category_names_flat
from src.db.queries.embeddings import find_similar
from src.db.queries.transactions import list_transactions
from src.models.annotation import Annotation, AnnotationCreate, AutoAnnotateResult
from src.pipeline.embed import build_embed_text, get_embedding_single
from src.pipeline.llm import annotate_transaction_llm, annotate_transaction_llm_with_examples
from src.pipeline.rules import apply_rules


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

    if transaction_ids:
        all_txns = list_transactions(conn, statement_id=statement_id)
        id_set = set(transaction_ids)
        all_txns = [t for t in all_txns if t["id"] in id_set]
    else:
        all_txns = list_transactions(conn, statement_id=statement_id)

    unannotated: list[dict] = []
    already_annotated_count = 0
    for txn in all_txns:
        if get_annotation_by_transaction(conn, txn["id"]) is not None:
            already_annotated_count += 1
        else:
            unannotated.append(txn)

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

    def _persist(ann_create: AnnotationCreate) -> None:
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
        conn.commit()

    # --- Stage 1: Rule pass ---
    logger.info("stage 1 | rules | %d txns", len(unannotated))
    needs_rag: list[dict] = []
    for txn in unannotated:
        result = apply_rules(txn)
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
                ann = AnnotationCreate(
                    transaction_id=txn["id"],
                    merchant=llm_result.merchant,
                    category=llm_result.category,
                    subcategory=llm_result.subcategory,
                    tags=llm_result.tags,
                    confidence=llm_result.confidence,
                    source="llm",
                )
                _persist(ann)
                llm_annotated += 1
                if llm_result.confidence < settings.confidence_threshold:
                    low_confidence += 1
                logger.debug(
                    "llm result | txn=%s  → %s/%s  merchant=%r  conf=%.2f",
                    txn["id"], llm_result.category, llm_result.subcategory, llm_result.merchant, llm_result.confidence,
                )
            else:
                llm_failed += 1
                logger.warning("llm failed | txn=%s  desc=%r", txn["id"], txn["raw_description"])
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

    # rag_direct: top match above similarity threshold → copy its annotation directly
    top_match = similar[0]
    cosine_similarity = 1.0 - top_match["distance"]
    logger.debug(
        "rag | top match | txn=%s  similar_txn=%s  cosine_sim=%.4f  threshold=%.4f",
        txn["id"], top_match["transaction_id"], cosine_similarity, settings.rag_direct_threshold,
    )
    if cosine_similarity >= settings.rag_direct_threshold:
        donor_ann = get_annotation_by_transaction(conn, top_match["transaction_id"])
        if donor_ann:
            logger.debug(
                "rag_direct | txn=%s  → %s/%s  conf=%.4f",
                txn["id"], donor_ann["category"], donor_ann.get("subcategory"), cosine_similarity,
            )
            return AnnotationCreate(
                transaction_id=txn["id"],
                merchant=donor_ann.get("merchant"),
                category=donor_ann["category"],
                subcategory=donor_ann.get("subcategory"),
                tags=[t for t in donor_ann.get("tags", "").split(",") if t],
                confidence=round(cosine_similarity, 4),
                source="rag_direct",
            )

    # rag_prompted: inject similar examples as few-shot context into the LLM prompt
    examples = _build_examples_from_similar(conn, similar)
    logger.debug("rag_prompted | txn=%s  examples=%d", txn["id"], len(examples))
    if examples:
        llm_result = annotate_transaction_llm_with_examples(txn, category_list, examples)
        if llm_result is not None:
            logger.debug(
                "rag_prompted result | txn=%s  → %s/%s  conf=%.2f",
                txn["id"], llm_result.category, llm_result.subcategory, llm_result.confidence,
            )
            return AnnotationCreate(
                transaction_id=txn["id"],
                merchant=llm_result.merchant,
                category=llm_result.category,
                subcategory=llm_result.subcategory,
                tags=llm_result.tags,
                confidence=llm_result.confidence,
                source="rag_prompted",
            )
        logger.warning("rag_prompted | llm returned nothing | txn=%s", txn["id"])

    return None


def _build_examples_from_similar(
    conn: sqlite3.Connection,
    similar: list[dict],
) -> list[dict]:
    """Fetch transaction + annotation details for similar matches to use as few-shot examples."""
    examples = []
    for match in similar:
        txn_row = conn.execute(
            "SELECT * FROM transactions WHERE id = ?",
            (match["transaction_id"],),
        ).fetchone()
        ann_row = get_annotation_by_transaction(conn, match["transaction_id"])
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
        })
    return examples
