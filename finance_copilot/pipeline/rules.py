"""Rule-based annotation engine: keyword/merchant matching before falling back to LLM."""
from __future__ import annotations

import json
import re
from functools import cache

from finance_copilot.config import settings
from finance_copilot.models.annotation import AnnotationCreate
from finance_copilot.parsers.description import (
    is_aggregator_note,
    is_known_shape,
    parse_description,
)
from finance_copilot.pipeline.merchant_catalog import (
    DISAMBIGUATION_RULES,
    MERCHANT_RULES,
    DisambiguationRule,
    MerchantRule,
)

__all__ = ["DISAMBIGUATION_RULES", "MERCHANT_RULES", "DisambiguationRule", "MerchantRule", "apply_rules"]

# Patterns at least this long (letters/digits only) also match a compacted payee
# name by prefix. Shorter ones ('ola', 'lic', 'jio') stay strictly word-bounded,
# which is where substring false positives live.
_COMPACT_MIN = 5
# A payee name this long (compacted) may be the bank's truncation of a longer
# name, so a pattern that merely *starts with* it can still be that merchant.
_TRUNCATED_MIN = 12

# Money coming *in* from a merchant is a refund or reversal, not spending at it:
# on credits only rules that describe incoming money may fire.
_CREDIT_CATEGORIES = frozenset({"Income", "Transfers", "Self Transfers", "Investments"})


def _allowed(category: str, debit_credit: str | None) -> bool:
    return debit_credit != "credit" or category in _CREDIT_CATEGORIES


@cache
def _compile_pattern(pattern: str) -> re.Pattern:
    """Compile a keyword to a word-boundary regex so 'emi' can't match 'premium'.

    Lookarounds instead of \\b so patterns that start/end with non-word chars
    (e.g. 'd-mart', 'cult.fit') still anchor correctly.
    """
    return re.compile(r"(?<!\w)" + re.escape(pattern.strip().lower()) + r"(?!\w)")


def _pattern_matches(pattern: str, haystack: str) -> bool:
    return _compile_pattern(pattern).search(haystack) is not None


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _compact_prefix_match(pattern: str, payee_compact: str) -> bool:
    """Spacing and truncation variants the word-bounded match can't see.

    - A multi-word pattern written without its spaces: 'URBANCOMPANY' ~
      'urban company'. Single words stay word-bounded ('AGODAX' is not 'agoda').
    - A payee cut off at the bank's print width: 'ROPPEN TRANSPOR' ~
      'roppen transportation'.
    """
    pat = _compact(pattern)
    if len(pat) < _COMPACT_MIN or not payee_compact:
        return False
    if " " in pattern.strip() and payee_compact.startswith(pat):
        return True
    return len(payee_compact) >= _TRUNCATED_MIN and pat.startswith(payee_compact)


def _matches(pattern: str, haystack: str, payee_compact: str) -> bool:
    return _pattern_matches(pattern, haystack) or _compact_prefix_match(pattern, payee_compact)


def _extract_upi_note(txn: dict) -> str:
    """Pull the UPI note out of upi_meta JSON, returning empty string if absent."""
    upi_meta = txn.get("upi_meta")
    if not upi_meta:
        return ""
    try:
        meta = json.loads(upi_meta) if isinstance(upi_meta, str) else upi_meta
        return str(meta.get("note", ""))
    except (json.JSONDecodeError, AttributeError):
        return ""


def apply_rules(txn: dict) -> AnnotationCreate | None:
    """Try to match the transaction against MERCHANT_RULES.

    Returns an AnnotationCreate with confidence=0.95 and source='model' on first match,
    or None if no rule matches.
    """
    raw = txn.get("raw_description") or ""
    parsed = parse_description(raw, settings.upi_noise_keywords)
    payee_compact = ""
    if is_known_shape(parsed) and parsed.counterparty:
        # Match what the line says, not how the bank encodes it: payee and note
        # only, so a bank prefix (HDFC, YESB) or a reference number can never
        # trigger a rule, and a payment-app note ('Pay To BharatPe') can't
        # impersonate the merchant.
        note = "" if is_aggregator_note(parsed.note) else (parsed.note or "")
        description = f"{parsed.counterparty} {note}".strip()
        upi_note = note
        payee_compact = _compact(parsed.counterparty)
    else:
        description = raw
        upi_note = _extract_upi_note(txn)

    field_values: dict[str, str] = {
        "raw_description": description.lower(),
        "upi_note": upi_note.lower(),
    }

    # Phase 1: disambiguation rules (more specific — checked before MERCHANT_RULES)
    for drule in DISAMBIGUATION_RULES:
        haystack = " ".join(field_values[f] for f in drule.match_fields if f in field_values)
        if not _allowed(drule.category, txn.get("debit_credit")):
            continue
        if any(_matches(p, haystack, payee_compact) for p in drule.base_patterns):
            if any(_pattern_matches(p, haystack) for p in drule.override_patterns):
                return AnnotationCreate(
                    transaction_id=txn["id"],
                    merchant=drule.merchant,
                    category=drule.category,
                    subcategory=drule.subcategory,
                    tags=drule.tags,
                    confidence=0.95,
                    source="rule",
                )

    # Phase 2: standard merchant rules
    for rule in MERCHANT_RULES:
        if not _allowed(rule.category, txn.get("debit_credit")):
            continue
        haystack = " ".join(field_values[f] for f in rule.match_fields if f in field_values)
        for pattern in rule.patterns:
            if _matches(pattern, haystack, payee_compact):
                return AnnotationCreate(
                    transaction_id=txn["id"],
                    merchant=rule.merchant,
                    category=rule.category,
                    subcategory=rule.subcategory,
                    tags=rule.tags,
                    confidence=0.95,
                    source="rule",
                )

    return None
