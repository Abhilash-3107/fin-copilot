"""Auto-annotation pipeline: rules first, then LLM for remainder."""
from __future__ import annotations

import sqlite3

from src.config import settings
from src.db.queries.annotations import get_annotation_by_transaction, insert_annotation
from src.db.queries.categories import get_category_names_flat
from src.db.queries.transactions import list_transactions
from src.models.annotation import Annotation, AnnotationCreate, AutoAnnotateResult
from src.pipeline.llm import annotate_transaction_llm
from src.pipeline.rules import apply_rules


def auto_annotate(
    conn: sqlite3.Connection,
    statement_id: str | None = None,
    transaction_ids: list[str] | None = None,
) -> AutoAnnotateResult:
    """Run the full auto-annotation pipeline on unannotated transactions.

    Fetches transactions that have no annotation, runs rule matching first,
    then falls back to LLM for the remainder. Persists all annotations in one commit.
    """
    # Fetch all transactions matching scope filters (no unannotated filter here so
    # we can count already_annotated accurately when transaction_ids are provided)
    if transaction_ids:
        # Filter to the explicitly requested IDs, then check annotation status ourselves
        all_txns = list_transactions(conn, statement_id=statement_id)
        id_set = set(transaction_ids)
        all_txns = [t for t in all_txns if t["id"] in id_set]
    else:
        all_txns = list_transactions(conn, statement_id=statement_id)

    # Split into already-annotated vs unannotated
    unannotated: list[dict] = []
    already_annotated_count = 0
    for txn in all_txns:
        if get_annotation_by_transaction(conn, txn["id"]) is not None:
            already_annotated_count += 1
        else:
            unannotated.append(txn)

    rule_matched = 0
    llm_annotated = 0
    llm_failed = 0
    low_confidence = 0

    annotations_to_insert: list[AnnotationCreate] = []

    # --- Rule pass ---
    needs_llm: list[dict] = []
    for txn in unannotated:
        result = apply_rules(txn)
        if result is not None:
            annotations_to_insert.append(result)
            rule_matched += 1
            if result.confidence < settings.confidence_threshold:
                low_confidence += 1
        else:
            needs_llm.append(txn)

    # --- LLM pass ---
    if needs_llm:
        category_list = get_category_names_flat(conn)
        for txn in needs_llm:
            llm_result = annotate_transaction_llm(txn, category_list)
            if llm_result is not None:
                ann = AnnotationCreate(
                    transaction_id=txn["id"],
                    merchant=llm_result.merchant,
                    category=llm_result.category,
                    subcategory=llm_result.subcategory,
                    tags=llm_result.tags,
                    confidence=llm_result.confidence,
                    source="model",
                )
                annotations_to_insert.append(ann)
                llm_annotated += 1
                if llm_result.confidence < settings.confidence_threshold:
                    low_confidence += 1
            else:
                llm_failed += 1

    # --- Persist ---
    for ann_create in annotations_to_insert:
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

    return AutoAnnotateResult(
        total_processed=len(unannotated),
        rule_matched=rule_matched,
        llm_annotated=llm_annotated,
        llm_failed=llm_failed,
        low_confidence=low_confidence,
        already_annotated=already_annotated_count,
    )
