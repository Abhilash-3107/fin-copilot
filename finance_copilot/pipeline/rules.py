"""Rule-based annotation engine: built-in merchant knowledge (merchant_catalog)
matched against what a transaction line says."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache

from finance_copilot.config import settings
from finance_copilot.models.annotation import AnnotationCreate
from finance_copilot.parsers.description import is_known_shape, parse_description
from finance_copilot.pipeline.merchant_catalog import (
    CATEGORY_DIRECTIONS,
    DISAMBIGUATION_RULES,
    MERCHANT_RULES,
    SPENDING,
    DisambiguationRule,
    MerchantRule,
    strip_payment_app,
)

__all__ = ["DISAMBIGUATION_RULES", "MERCHANT_RULES", "DisambiguationRule", "MerchantRule", "apply_rules"]

RULE_CONFIDENCE = 0.95


@cache
def _compile_pattern(pattern: str) -> re.Pattern:
    """A keyword as a word-bounded regex, so 'emi' can't match 'premium'.

    Lookarounds instead of \\b so patterns that start/end with non-word chars
    (e.g. 'd-mart', 'cult.fit') still anchor correctly.
    """
    return re.compile(r"(?<!\w)" + re.escape(pattern.strip().lower()) + r"(?!\w)")


def _pattern_matches(pattern: str, haystack: str) -> bool:
    return _compile_pattern(pattern).search(haystack) is not None


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass(frozen=True)
class MatchView:
    """What rules (built-in and user-written) match against, lowercased.

    For a UPI line the parser understands: the payee and the note only, so a
    bank prefix (HDFC, YESB) or a reference number never triggers a rule, and a
    payment-app phrase ('Pay To BharatPe') never impersonates the merchant.
    Other lines: the raw description and stored UPI note.
    """
    description: str             # payee + note (UPI) or the raw line
    note: str
    payee: str | None            # parsed payee as printed, UPI lines only
    payee_prefixes: frozenset[str]  # compacted leading words: 'urban', 'urbancompany', ...

    def haystack(self, fields: list[str]) -> str:
        return " ".join({"raw_description": self.description, "upi_note": self.note}[f] for f in fields)


def match_view(txn: dict) -> MatchView:
    raw = txn["raw_description"]
    parsed = parse_description(raw, settings.upi_noise_keywords)
    if is_known_shape(parsed) and parsed.counterparty:
        note = strip_payment_app(parsed.note) or ""
        words = [_compact(w) for w in parsed.counterparty.split()]
        prefixes = frozenset("".join(words[: i + 1]) for i in range(len(words)))
        return MatchView(f"{parsed.counterparty} {note}".strip().lower(), note.lower(), parsed.counterparty, prefixes)
    note = json.loads(txn["upi_meta"])["note"] or "" if txn.get("upi_meta") else ""
    return MatchView(raw.lower(), note.lower(), None, frozenset())


@dataclass(frozen=True)
class _Compiled:
    """A catalog rule with its patterns compiled once at import."""
    regex: re.Pattern
    multiword_compact: frozenset[str]  # compacted multi-word patterns, entity rules only


def _compile_rule(patterns: list[str], merchant: str | None) -> _Compiled:
    regex = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(p.strip().lower()) for p in patterns) + r")(?!\w)"
    )
    multiword = frozenset(_compact(p) for p in patterns if " " in p.strip()) if merchant else frozenset()
    return _Compiled(regex, multiword)


_MERCHANTS = [(rule, _compile_rule(rule.patterns, rule.merchant)) for rule in MERCHANT_RULES]
_DISAMBIGUATIONS = [
    (rule, _compile_rule(rule.base_patterns, rule.merchant), _compile_rule(rule.override_patterns, None))
    for rule in DISAMBIGUATION_RULES
]


def _names_payee(compiled: _Compiled, view: MatchView) -> bool:
    """A merchant-naming pattern the word-bounded search can't see: the payee's
    leading words run the phrase together ('URBANCOMPANY'), or the payee was cut
    at the bank's print width inside it ('ROPPEN TRANSPOR')."""
    if not compiled.multiword_compact or view.payee is None:
        return False
    if compiled.multiword_compact & view.payee_prefixes:
        return True
    if len(view.payee) < settings.counterparty_truncated_min_len:
        return False
    cut = _compact(view.payee)
    return any(p.startswith(cut) for p in compiled.multiword_compact)


def _hits(compiled: _Compiled, view: MatchView, fields: list[str]) -> bool:
    return compiled.regex.search(view.haystack(fields)) is not None or _names_payee(compiled, view)


def _allowed(category: str, debit_credit: str) -> bool:
    return debit_credit in CATEGORY_DIRECTIONS.get(category, SPENDING)


def _annotation(txn: dict, rule: MerchantRule | DisambiguationRule) -> AnnotationCreate:
    return AnnotationCreate(
        transaction_id=txn["id"], merchant=rule.merchant, category=rule.category,
        subcategory=rule.subcategory, tags=rule.tags, confidence=RULE_CONFIDENCE, source="rule",
    )


def apply_rules(txn: dict) -> AnnotationCreate | None:
    """The first catalog rule that matches the transaction (source 'rule'), or None.

    Disambiguation rules go first (more specific), then merchant rules in
    catalog order. A rule only fires in the directions its category describes.
    """
    view = match_view(txn)
    direction = txn["debit_credit"]
    for rule, base, override in _DISAMBIGUATIONS:
        if (_allowed(rule.category, direction) and _hits(base, view, rule.match_fields)
                and override.regex.search(view.haystack(rule.match_fields))):
            return _annotation(txn, rule)
    for rule, compiled in _MERCHANTS:
        if _allowed(rule.category, direction) and _hits(compiled, view, rule.match_fields):
            return _annotation(txn, rule)
    return None
