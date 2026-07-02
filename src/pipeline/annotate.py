"""Auto-annotation pipeline: rules → RAG direct → RAG prompted → plain LLM."""
from __future__ import annotations

import difflib
import json
import logging
import sqlite3
from collections import Counter

logger = logging.getLogger(__name__)

from src.config import settings
from src.db.queries.annotations import insert_annotation
from src.db.queries.app_settings import get_dev_mode
from src.db.queries.common import dump_string_list, parse_string_list
from src.db.queries.categories import get_category_names_flat
from src.db.queries.embeddings import find_similar
from src.db.queries.people import list_people
from src.db.queries.transactions import list_transactions
from src.models.annotation import (
    Annotation,
    AnnotationCreate,
    AutoAnnotateResult,
    ReasoningTrace,
    TraceNeighbour,
)
from src.models.transaction import TxnRow
from src.pipeline.calibration import get_calibrated_dampening
from src.pipeline.counterparty import CounterpartyPrior, counterparty_prior
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


def _normalize_subcategory(
    category: str, subcategory: str | None, category_list: list[str]
) -> str | None:
    """Validate an LLM-returned subcategory against the taxonomy for its category.

    The JSON schema only constrains the top-level category; subcategory is free
    text, so a small model can invent one. Exact/case-insensitive match → keep;
    otherwise drop to None rather than persist a label outside the taxonomy.
    """
    if not subcategory:
        return None
    prefix = f"{category} > "
    valid = [c[len(prefix):] for c in category_list if c.startswith(prefix)]
    if subcategory in valid:
        return subcategory
    lower = subcategory.strip().lower()
    for v in valid:
        if v.lower() == lower:
            return v
    logger.warning("subcategory invalid | %r not under %r → None", subcategory, category)
    return None


def _match_known_person(txn: TxnRow, known_people: list[tuple[str, str]]) -> AnnotationCreate | None:
    """Return a Peer Transfer annotation if the transaction matches a known person.

    known_people is a list of (display_name, match_token) where match_token is the
    person's UPI handle, already lowercased. When the transaction has an extracted
    counterparty VPA, match it exactly; otherwise fall back to substring search in
    the description (older rows ingested before VPA extraction).
    """
    vpa = None
    upi_meta = txn.get("upi_meta")
    if upi_meta:
        try:
            meta = json.loads(upi_meta) if isinstance(upi_meta, str) else upi_meta
            vpa = (meta.get("vpa") or "").lower() or None
        except (json.JSONDecodeError, AttributeError):
            pass

    desc = (txn.get("raw_description") or "").lower()
    for name, token in known_people:
        matched = vpa == token if vpa else token in desc
        if matched:
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
    progress_cb=None,
) -> AutoAnnotateResult:
    """Run the full auto-annotation pipeline on unannotated transactions.

    Stage 1 — rules:        keyword/merchant match → source=rule
    Stage 2 — rag_direct:   cosine similarity >= threshold → copy annotation → source=rag_direct
    Stage 3 — rag_prompted: cosine similarity found but below threshold → LLM with examples → source=rag_prompted
    Stage 4 — llm:          no similar found or embedding unavailable → plain LLM → source=llm

    progress_cb(processed, total), when given, is called after each transaction
    so callers (e.g. background jobs) can report progress.
    """
    logger.info("auto_annotate start | statement_id=%s", statement_id)

    # Runtime, UI-toggleable: capture the reasoning trace only when dev mode is on.
    # Read once per run (the setting won't flip mid-run).
    dev_mode = get_dev_mode(conn)

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

    def _persist(ann_create: AnnotationCreate, trace: ReasoningTrace | None = None) -> None:
        nonlocal uncommitted
        # Only store the reasoning trace in dev mode — regular runs leave it NULL.
        reasoning = trace.model_dump_json() if (trace is not None and dev_mode) else None
        annotation = Annotation(
            transaction_id=ann_create.transaction_id,
            merchant=ann_create.merchant,
            category=ann_create.category,
            subcategory=ann_create.subcategory,
            tags=dump_string_list(ann_create.tags),
            confidence=ann_create.confidence,
            source=ann_create.source,
            reasoning=reasoning,
        )
        insert_annotation(conn, annotation)
        uncommitted += 1
        if uncommitted >= _COMMIT_EVERY:
            conn.commit()
            uncommitted = 0

    # Load known people once — used for peer transfer matching before merchant rules
    known_people = [(p["name"], p["upi"].lower()) for p in list_people(conn) if p.get("upi")]

    # A transaction counts as processed once its final outcome is decided
    # (annotated or failed) — stage-1 misses are still pending.
    total = len(unannotated)
    processed = 0

    def _tick() -> None:
        nonlocal processed
        processed += 1
        if progress_cb is not None:
            progress_cb(processed, total)

    # --- Stage 1: Rule pass ---
    logger.info("stage 1 | rules | %d txns", len(unannotated))
    needs_rag: list[dict] = []
    for txn in unannotated:
        result = _match_known_person(txn, known_people) or apply_rules(txn)
        if result is not None:
            trace = ReasoningTrace(
                stage="rule",
                final_confidence=result.confidence,
                matched_rule=result.merchant or result.category,
            )
            _persist(result, trace)
            _tick()
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
            rag_result, rag_trace = _try_rag_annotation(conn, txn, category_list)
            if rag_result is not None:
                _persist(rag_result, rag_trace)
                _tick()
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
        # Recurring transactions share normalized descriptions — call the LLM once
        # per (description, direction) and reuse the result within this run.
        llm_cache: dict = {}
        for txn in needs_llm:
            logger.debug("llm | txn=%s  desc=%r  amount=%.2f", txn["id"], txn["raw_description"], txn["amount"])
            cache_key = (
                (txn.get("raw_description") or "").strip().lower(),
                txn.get("debit_credit") or "",
            )
            llm_result = llm_cache.get(cache_key)
            if llm_result is None:
                llm_result = annotate_transaction_llm(txn, category_list)
                if llm_result is not None and cache_key[0]:
                    llm_cache[cache_key] = llm_result
            else:
                logger.debug("llm cache hit | txn=%s", txn["id"])
            if llm_result is not None:
                category = _normalize_category(llm_result.category, category_list)
                dampening = get_calibrated_dampening(conn, "llm", category)
                ann = AnnotationCreate(
                    transaction_id=txn["id"],
                    merchant=llm_result.merchant,
                    category=category,
                    subcategory=_normalize_subcategory(category, llm_result.subcategory, category_list),
                    tags=llm_result.tags,
                    confidence=round(llm_result.confidence * dampening, 4),
                    source="llm",
                )
                trace = ReasoningTrace(
                    stage="llm",
                    final_confidence=ann.confidence,
                    llm_reasoning=llm_result.reasoning,
                    raw_confidence=llm_result.confidence,
                    dampening_factor=round(dampening, 4),
                )
                _persist(ann, trace)
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
            _tick()

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


_TRUSTED_SOURCES = {"manual", "rule", "imported"}


def _donor_weight(source: str | None) -> float:
    """Vote weight for a donor based on its annotation source.

    Human-verified / rule donors carry full weight; machine guesses (llm, rag_*)
    are downweighted so they cannot out-vote a human label or let one recurring
    machine-labeled merchant dominate the vote.
    """
    return 1.0 if source in _TRUSTED_SOURCES else settings.rag_machine_donor_weight


def _dedup_donors(annotated_matches: list[dict]) -> list[dict]:
    """Collapse donors that refer to the same counterparty to a single vote.

    Amount-clustered retrieval often returns the same recurring merchant several
    times (e.g. DISTRICT DINING ×3); counting each as an independent vote inflates
    its category's apparent agreement. Group by a stable counterparty key (UPI VPA,
    else canonical merchant, else normalized description) and keep only the nearest
    (smallest-distance) donor per group.
    """
    best_by_key: dict[str, dict] = {}
    for m in annotated_matches:
        key = _counterparty_key(m)
        existing = best_by_key.get(key)
        if existing is None or m["distance"] < existing["distance"]:
            best_by_key[key] = m
    # Preserve ascending-distance order for downstream margin logic.
    return sorted(best_by_key.values(), key=lambda m: m["distance"])


def _counterparty_key(match: dict) -> str:
    """Stable identity for a donor: UPI VPA → merchant → normalized description."""
    ann = match.get("annotation") or {}
    vpa = None
    upi_meta = match.get("upi_meta")
    if upi_meta:
        try:
            meta = json.loads(upi_meta) if isinstance(upi_meta, str) else upi_meta
            vpa = (meta.get("vpa") or "").lower() or None
        except (json.JSONDecodeError, AttributeError):
            pass
    if vpa:
        return f"vpa:{vpa}"
    merchant = (ann.get("merchant") or "").strip().lower()
    if merchant:
        return f"merchant:{merchant}"
    return f"desc:{(match.get('raw_description') or match['transaction_id']).strip().lower()}"


def _weighted_trusted_vote(annotated_matches: list[dict]) -> tuple[str | None, float, float]:
    """Aggregate donor categories into a source-weighted, deduplicated vote.

    Literature consistently finds majority/consensus aggregation more reliable than
    the single nearest neighbour (RankRAG 2024; VoteGCL 2026), so confidence and
    routing key off this vote rather than top-1 similarity.

    Returns (winning_category, winning_share, trusted_total_weight) where
    winning_share is the winner's fraction of the total weighted vote in [0,1].
    """
    if not annotated_matches:
        return None, 0.0, 0.0
    weights: dict[str, float] = {}
    trusted_weight = 0.0
    for m in annotated_matches:
        cat = m.get("category")
        if not cat:
            continue
        w = _donor_weight(m.get("source"))
        weights[cat] = weights.get(cat, 0.0) + w
        if m.get("source") in _TRUSTED_SOURCES:
            trusted_weight += w
    if not weights:
        return None, 0.0, 0.0
    total = sum(weights.values())
    winner, winner_weight = max(weights.items(), key=lambda kv: kv[1])
    return winner, winner_weight / total, trusted_weight


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


def _fuse_counterparty_prior(
    confidence: float,
    chosen_category: str,
    prior: CounterpartyPrior,
) -> tuple[float, str]:
    """Late-fuse the counterparty recurrence prior into a rag_prompted confidence.

    Out-of-band signal: the embedding/KNN can't see that a counterparty recurs, but
    the user's own history can. We adjust *confidence/routing only* — never the label
    — consistent with the prior being a weak-but-orthogonal evidence source.

    - established prior AGREES with the chosen label → "rescue": lift confidence
      toward prior.probability so genuine recurring transfers (which category-level
      calibration over-punishes, e.g. KARABI BORA) clear the review threshold.
    - established prior DISAGREES → "tighten": cap below threshold so it routes to
      review. Catches both the cab-misfire and a recurring contact's occasional
      off-category spend (the irreducible ~15% the floor can't fix).
    - not established (cold start / first-time counterparty) → "neutral": no change.

    Returns (new_confidence, effect) where effect ∈ {"rescue","tighten","neutral"}.
    """
    if not settings.counterparty_prior_enabled or not prior.established:
        return confidence, "neutral"

    if prior.category == chosen_category:
        # Rescue: don't reduce an already-confident score; only raise a dampened one,
        # bounded by how strongly the counterparty supports this category.
        rescued = max(confidence, round(prior.probability, 4))
        return rescued, ("rescue" if rescued > confidence else "neutral")

    # Disagreement: an established counterparty prior contradicts the per-txn pick.
    capped = min(confidence, settings.rag_defer_confidence_cap)
    return capped, ("tighten" if capped < confidence else "neutral")


def _try_rag_annotation(
    conn: sqlite3.Connection,
    txn: TxnRow,
    category_list: list[str],
    before_txn_date: str | None = None,
) -> tuple[AnnotationCreate | None, ReasoningTrace | None]:
    """Attempt RAG-based annotation.

    Returns (AnnotationCreate, ReasoningTrace) with source='rag_direct',
    'rag_knn' or 'rag_prompted', or (None, None) if the embedding service is
    unavailable or no similar annotated transactions exist (caller falls through
    to plain LLM).

    before_txn_date is set only by the time-split eval harness: it restricts
    retrieval and the counterparty prior to history strictly before that date.
    """
    try:
        embed_text = build_embed_text(txn)
        query_vec = get_embedding_single(embed_text)
    except Exception as e:
        logger.warning("rag | embedding failed | txn=%s  error=%s", txn["id"], e)
        return None, None  # embedding service down — fall through to plain LLM

    # With diversity-aware example selection on, fetch a wider candidate pool so
    # there is something to diversify over; vote/margin logic still sees only the
    # usual top-K (identical semantics either way).
    fetch_k = settings.rag_top_k * 3 if settings.rag_example_diversity else settings.rag_top_k
    similar_wide = find_similar(
        conn,
        query_vec,
        top_k=fetch_k,
        exclude_transaction_ids=[txn["id"]],
        before_txn_date=before_txn_date,
    )
    similar = similar_wide[: settings.rag_top_k]

    if not similar:
        logger.debug("rag | no similar found | txn=%s  desc=%r", txn["id"], txn["raw_description"])
        return None, None

    # Novelty gate: if the best match is too far away, the examples are noise
    best_similarity = 1.0 - similar[0]["distance"]
    if best_similarity < settings.rag_similarity_floor:
        logger.debug(
            "rag | novelty gate | txn=%s  best_sim=%.4f < floor=%.4f → skip RAG",
            txn["id"], best_similarity, settings.rag_similarity_floor,
        )
        return None, None

    # Fetch annotations for all top-K matches upfront (used for agreement + margin analysis)
    top_match = similar[0]
    cosine_similarity = best_similarity
    ann_by_txn = _annotations_by_transaction(conn, [m["transaction_id"] for m in similar])
    # Counterparty metadata (VPA, description) for the donors, used to dedup the vote.
    donor_txn_by_id = _donor_transactions([m["transaction_id"] for m in similar], conn)
    annotated_matches = []
    for match in similar:
        ann = ann_by_txn.get(match["transaction_id"])
        if ann:
            donor_txn = donor_txn_by_id.get(match["transaction_id"], {})
            annotated_matches.append({
                "transaction_id": match["transaction_id"],
                "distance": match["distance"],
                "category": ann.get("category"),
                "source": ann.get("source"),
                "annotation": ann,
                "upi_meta": donor_txn.get("upi_meta"),
                "raw_description": donor_txn.get("raw_description"),
            })

    # Deduplicate recurring counterparties to one vote before any agreement logic.
    annotated_matches = _dedup_donors(annotated_matches)

    logger.debug(
        "rag | top match | txn=%s  similar_txn=%s  cosine_sim=%.4f  threshold=%.4f  annotated_k=%d",
        txn["id"], top_match["transaction_id"], cosine_similarity, settings.rag_direct_threshold, len(annotated_matches),
    )

    # Source-weighted, deduplicated vote across the donors — the basis for
    # confidence and the reject/defer decision (more reliable than top-1 similarity).
    vote_category, vote_share, trusted_weight = _weighted_trusted_vote(annotated_matches)

    # Dev-mode trace: the deduped neighbours behind this decision, with their
    # cosine similarity. Built once and shared by both the rag_direct and
    # rag_prompted branches below (only serialized when settings.dev_mode is on).
    trace_neighbours = [
        TraceNeighbour(
            transaction_id=m["transaction_id"],
            raw_description=m.get("raw_description"),
            category=m.get("category"),
            source=m.get("source"),
            distance=round(m["distance"], 4),
            similarity=round(1.0 - m["distance"], 4),
        )
        for m in annotated_matches
    ]

    # rag_direct: top match above similarity threshold → copy its annotation directly
    # Only trust human-verified or rule-matched annotations;
    # LLM/RAG-sourced donors fall through to rag_prompted so the LLM re-evaluates.
    if cosine_similarity >= settings.rag_direct_threshold:
        donor_ann = annotated_matches[0]["annotation"] if annotated_matches else None
        if donor_ann and donor_ann.get("source") in _TRUSTED_SOURCES:
            top_category = donor_ann["category"]

            # Agreement factor: penalize if top-K matches disagree on category
            agreement_factor = _compute_agreement_factor(annotated_matches, top_category)

            # Margin factor: penalize if nearest different-category match is close.
            # Use the deduped nearest donor's distance so both sides of the margin
            # come from the same (post-dedup) donor set.
            nearest_distance = annotated_matches[0]["distance"]
            next_diff_distance = next(
                (m["distance"] for m in annotated_matches if m.get("category") != top_category),
                None,
            )
            margin_factor = _compute_margin_factor(nearest_distance, next_diff_distance)

            confidence = round(cosine_similarity * agreement_factor * margin_factor, 4)
            logger.debug(
                "rag_direct | txn=%s  → %s/%s  cosine=%.4f  agreement=%.4f  margin=%.4f  conf=%.4f  donor_source=%s",
                txn["id"], top_category, donor_ann.get("subcategory"),
                cosine_similarity, agreement_factor, margin_factor, confidence, donor_ann["source"],
            )
            trace = ReasoningTrace(
                stage="rag_direct",
                final_confidence=confidence,
                best_similarity=round(cosine_similarity, 4),
                neighbours=trace_neighbours,
                vote_category=vote_category,
                vote_share=round(vote_share, 4) if vote_share is not None else None,
                trusted_weight=round(trusted_weight, 4) if trusted_weight is not None else None,
                agreement_factor=round(agreement_factor, 4),
                margin_factor=round(margin_factor, 4),
            )
            return AnnotationCreate(
                transaction_id=txn["id"],
                merchant=donor_ann.get("merchant"),
                category=top_category,
                subcategory=donor_ann.get("subcategory"),
                tags=parse_string_list(donor_ann.get("tags")),
                confidence=confidence,
                source="rag_direct",
            ), trace
        elif donor_ann:
            logger.debug(
                "rag_direct skipped | txn=%s  donor_source=%s (untrusted) → falling through to rag_prompted",
                txn["id"], donor_ann.get("source"),
            )

    # Stage 2.5 (experimental, settings.rag_knn_enabled): a decisive trusted kNN
    # vote is accepted without any LLM call. Distance-weighted voting over
    # trusted neighbours is strictly more robust than single-donor copy
    # (kNN-LM-style), and at ~1 ms it removes the LLM from the loop for the
    # recurring bulk of a mature user's transactions.
    if (
        settings.rag_knn_enabled
        and vote_category
        and best_similarity >= settings.rag_knn_similarity_floor
        and vote_share >= settings.rag_knn_vote_share
        and trusted_weight >= settings.rag_knn_min_trusted_weight
    ):
        donor = next(
            (
                m for m in annotated_matches
                if m.get("category") == vote_category and m.get("source") in _TRUSTED_SOURCES
            ),
            None,
        )
        if donor is not None:
            donor_ann = donor["annotation"]
            confidence = round(best_similarity * vote_share, 4)
            logger.debug(
                "rag_knn | txn=%s  → %s  best_sim=%.4f  vote_share=%.4f  conf=%.4f",
                txn["id"], vote_category, best_similarity, vote_share, confidence,
            )
            trace = ReasoningTrace(
                stage="rag_knn",
                final_confidence=confidence,
                best_similarity=round(best_similarity, 4),
                neighbours=trace_neighbours,
                vote_category=vote_category,
                vote_share=round(vote_share, 4),
                trusted_weight=round(trusted_weight, 4),
            )
            return AnnotationCreate(
                transaction_id=txn["id"],
                merchant=donor_ann.get("merchant"),
                category=vote_category,
                subcategory=donor_ann.get("subcategory"),
                tags=parse_string_list(donor_ann.get("tags")),
                confidence=confidence,
                source="rag_knn",
            ), trace

    # rag_prompted: inject similar examples as few-shot context into the LLM prompt
    if settings.rag_example_diversity:
        ann_by_txn_wide = _annotations_by_transaction(
            conn, [m["transaction_id"] for m in similar_wide]
        )
        examples = _build_examples_from_similar(conn, similar_wide, ann_by_txn_wide)
    else:
        examples = _build_examples_from_similar(conn, similar, ann_by_txn)
    logger.debug("rag_prompted | txn=%s  examples=%d", txn["id"], len(examples))
    if examples:
        # Pass the example-category majority as a hint so the LLM weighs the
        # retrieved neighbors over its pretraining prior. Use the categories the
        # LLM actually sees (the selected examples), so the off-example cap below
        # judges against the real prompt content.
        example_categories = [e["category"] for e in examples if e.get("category")]
        majority_category, majority_count = _majority_category(example_categories)
        llm_result = annotate_transaction_llm_with_examples(
            txn, category_list, examples, majority_category, majority_count
        )
        if llm_result is not None:
            category = _normalize_category(llm_result.category, category_list)
            dampening = get_calibrated_dampening(conn, "rag_prompted", category)
            confidence = round(llm_result.confidence * dampening, 4)
            caps_applied: list[str] = []

            # Seam A: counterparty recurrence prior — out-of-band, computed from the
            # user's own prior labels (causal: bounded by this txn's date, excludes
            # self). Inert for unknown/first-time counterparties.
            prior = counterparty_prior(conn, txn)

            # Off-example backstop: if the LLM picked a category that none of the
            # retrieved examples used, it's likely falling back on its prior rather
            # than the evidence. Don't override it (the example majority can itself
            # be a coincidence of amount), but cap confidence so it lands in the
            # review queue instead of being auto-accepted.
            if example_categories and category not in set(example_categories):
                capped = min(confidence, settings.rag_offexample_confidence_cap)
                logger.info(
                    "rag_prompted off-example | txn=%s  → %s (not in examples %s)  conf %.4f → %.4f",
                    txn["id"], category, sorted(set(example_categories)), confidence, capped,
                )
                confidence = capped
                caps_applied.append("off_example")

            # Reject/defer band: only when the LLM is *itself* uncertain AND the
            # trusted donors are split with no clear winner. This is the genuinely
            # undecidable case (e.g. an unfamiliar small UPI to a bare personal name
            # whose amount-neighbors are part cab, part family transfer) — the
            # description carries no signal and neither does history, so route to a
            # human. We deliberately do NOT defer when the LLM is confident (a
            # merchant it recognizes, e.g. 'Zomato', 'Miya Kebabs'); the noisy
            # amount-driven neighbor vote must not override a grounded decision.
            elif (
                trusted_weight > 0
                and vote_share < settings.rag_consensus_floor
                and llm_result.confidence < settings.rag_defer_llm_confidence
            ):
                capped = min(confidence, settings.rag_defer_confidence_cap)
                logger.info(
                    "rag_prompted defer | txn=%s  → %s  llm_conf=%.2f  trusted_vote_share=%.2f < floor=%.2f  conf %.4f → %.4f",
                    txn["id"], category, llm_result.confidence, vote_share,
                    settings.rag_consensus_floor, confidence, capped,
                )
                confidence = capped
                caps_applied.append("defer")

            # Seam B: fuse the counterparty prior. Applied after the existing caps so
            # it can rescue a genuine recurring transfer past category-calibration
            # dampening, or tighten further when an established prior disagrees with
            # the per-txn pick. Adjusts confidence/routing only, never the label.
            confidence, prior_effect = _fuse_counterparty_prior(confidence, category, prior)
            if prior_effect != "neutral":
                caps_applied.append(f"counterparty_{prior_effect}")
                logger.info(
                    "rag_prompted counterparty %s | txn=%s  → %s  prior=%s (n=%d p=%.2f)  conf→%.4f",
                    prior_effect, txn["id"], category, prior.category,
                    prior.n_prior, prior.probability, confidence,
                )

            logger.debug(
                "rag_prompted result | txn=%s  → %s/%s  raw_conf=%.2f  dampened_conf=%.4f",
                txn["id"], category, llm_result.subcategory, llm_result.confidence, confidence,
            )
            trace = ReasoningTrace(
                stage="rag_prompted",
                final_confidence=confidence,
                best_similarity=round(best_similarity, 4),
                neighbours=trace_neighbours,
                vote_category=vote_category,
                vote_share=round(vote_share, 4) if vote_share is not None else None,
                trusted_weight=round(trusted_weight, 4) if trusted_weight is not None else None,
                caps_applied=caps_applied,
                counterparty_prior_category=prior.category if prior.established else None,
                counterparty_prior_probability=prior.probability if prior.established else None,
                counterparty_prior_n=prior.n_prior if prior.established else None,
                counterparty_prior_effect=prior_effect,
                llm_reasoning=llm_result.reasoning,
                raw_confidence=llm_result.confidence,
                dampening_factor=round(dampening, 4),
            )
            return AnnotationCreate(
                transaction_id=txn["id"],
                merchant=llm_result.merchant,
                category=category,
                subcategory=_normalize_subcategory(category, llm_result.subcategory, category_list),
                tags=llm_result.tags,
                confidence=confidence,
                source="rag_prompted",
            ), trace
        logger.warning("rag_prompted | llm returned nothing | txn=%s", txn["id"])

    return None, None


def _majority_category(categories: list[str]) -> tuple[str | None, int]:
    """Return the most common category and its count, or (None, 0) if empty."""
    if not categories:
        return None, 0
    top_category, count = Counter(categories).most_common(1)[0]
    return top_category, count


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


def _donor_transactions(transaction_ids: list[str], conn: sqlite3.Connection) -> dict[str, dict]:
    """Fetch id, upi_meta, raw_description for donor transactions, keyed by id.

    Used to derive a stable counterparty key for vote deduplication.
    """
    if not transaction_ids:
        return {}
    placeholders = ",".join("?" * len(transaction_ids))
    rows = conn.execute(
        f"SELECT id, upi_meta, raw_description FROM transactions WHERE id IN ({placeholders})",
        transaction_ids,
    ).fetchall()
    return {row["id"]: dict(row) for row in rows}


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

    # Experimental (settings.rag_example_diversity): the raw top-K are often K
    # near-duplicates of one merchant/label — one bit of information that primes
    # the model. MMR-lite: keep the 2 nearest unconditionally, then prefer the
    # nearest example of each not-yet-represented category (retrieval order is
    # ascending distance, so iteration order encodes nearness).
    if settings.rag_example_diversity and len(examples) > 2:
        kept = examples[:2]
        seen_categories = {e["category"] for e in kept}
        rest = examples[2:]
        for e in rest:
            if len(kept) >= settings.rag_top_k:
                break
            if e["category"] not in seen_categories:
                kept.append(e)
                seen_categories.add(e["category"])
        for e in rest:
            if len(kept) >= settings.rag_top_k:
                break
            if e not in kept:
                kept.append(e)
        examples = kept

    # Prioritize human-verified examples first — LLMs are sensitive to example ordering
    _SOURCE_PRIORITY = {"manual": 0, "rule": 1, "imported": 2}
    examples.sort(key=lambda e: _SOURCE_PRIORITY.get(e.get("source", ""), 9))
    return examples
