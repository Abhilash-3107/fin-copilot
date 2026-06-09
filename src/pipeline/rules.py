"""Rule-based annotation engine: keyword/merchant matching before falling back to LLM."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from src.models.annotation import AnnotationCreate


@lru_cache(maxsize=None)
def _compile_pattern(pattern: str) -> re.Pattern:
    """Compile a keyword to a word-boundary regex so 'emi' can't match 'premium'.

    Lookarounds instead of \\b so patterns that start/end with non-word chars
    (e.g. 'd-mart', 'cult.fit') still anchor correctly.
    """
    return re.compile(r"(?<!\w)" + re.escape(pattern.strip().lower()) + r"(?!\w)")


def _pattern_matches(pattern: str, haystack: str) -> bool:
    return _compile_pattern(pattern).search(haystack) is not None


@dataclass
class DisambiguationRule:
    """Override a base rule when secondary patterns also match (more specific takes precedence)."""
    base_patterns: list[str]      # must match one of these first
    override_patterns: list[str]  # if ANY of these also match → use override
    category: str
    subcategory: str | None = None
    merchant: str | None = None
    tags: list[str] = field(default_factory=list)
    match_fields: list[str] = field(default_factory=lambda: ["raw_description", "upi_note"])


@dataclass
class MerchantRule:
    patterns: list[str]          # case-insensitive substrings to search for
    category: str
    subcategory: str | None = None
    merchant: str | None = None  # canonical merchant name
    tags: list[str] = field(default_factory=list)
    match_fields: list[str] = field(default_factory=lambda: ["raw_description", "upi_note"])


MERCHANT_RULES: list[MerchantRule] = [
    # --- Food Delivery ---
    MerchantRule(["swiggy"], "Food & Dining", "Food Delivery", "Swiggy", ["food"]),
    MerchantRule(["zomato"], "Food & Dining", "Food Delivery", "Zomato", ["food"]),
    MerchantRule(["instamart"], "Food & Dining", "Food Delivery", "Swiggy Instamart", ["grocery"]),

    # --- Groceries ---
    MerchantRule(["bigbasket", "big basket"], "Food & Dining", "Groceries", "BigBasket", ["grocery"]),
    MerchantRule(["blinkit", "grofers"], "Food & Dining", "Groceries", "Blinkit", ["grocery"]),
    MerchantRule(["zepto"], "Food & Dining", "Groceries", "Zepto", ["grocery"]),
    MerchantRule(["dmart", "d-mart"], "Food & Dining", "Groceries", "DMart", ["grocery"]),

    # --- Restaurants / Cafes ---
    MerchantRule(["starbucks"], "Food & Dining", "Cafe & Snacks", "Starbucks", ["coffee"]),
    MerchantRule(["cafe coffee day", "ccd"], "Food & Dining", "Cafe & Snacks", "Cafe Coffee Day", ["coffee"]),
    MerchantRule(["dominos", "domino's"], "Food & Dining", "Restaurants", "Domino's", ["food"]),
    MerchantRule(["mcdonald", "mcdonalds"], "Food & Dining", "Restaurants", "McDonald's", ["food"]),
    MerchantRule(["kfc"], "Food & Dining", "Restaurants", "KFC", ["food"]),

    # --- Online Shopping ---
    MerchantRule(["amazon", "amzn"], "Shopping", "Online Shopping", "Amazon", ["shopping"]),
    MerchantRule(["flipkart"], "Shopping", "Online Shopping", "Flipkart", ["shopping"]),
    MerchantRule(["myntra"], "Shopping", "Clothing & Apparel", "Myntra", ["shopping", "clothing"]),
    MerchantRule(["meesho"], "Shopping", "Online Shopping", "Meesho", ["shopping"]),
    MerchantRule(["ajio"], "Shopping", "Clothing & Apparel", "AJIO", ["shopping", "clothing"]),
    MerchantRule(["nykaa"], "Shopping", "General Retail", "Nykaa", ["shopping", "beauty"]),

    # --- Transport ---
    MerchantRule(["uber"], "Transport", "Cab & Auto", "Uber", ["transport"]),
    MerchantRule(["ola", "olacabs", "ola cabs"], "Transport", "Cab & Auto", "Ola", ["transport"]),
    MerchantRule(["rapido"], "Transport", "Cab & Auto", "Rapido", ["transport"]),
    MerchantRule(["irctc"], "Travel", "Train", "IRCTC", ["travel", "train"]),
    MerchantRule(["indigo", "air india", "spicejet", "akasa", "vistara", "go first"], "Travel", "Flights", None, ["travel", "flight"]),
    MerchantRule(["makemytrip", "mmt"], "Travel", None, "MakeMyTrip", ["travel"]),
    MerchantRule(["goibibo"], "Travel", None, "Goibibo", ["travel"]),
    MerchantRule(["redbus"], "Travel", "Bus", "redBus", ["travel", "bus"]),
    MerchantRule(["petrol", "petroleum", "hp petrol", "indian oil", "iocl", "bharat petroleum", "bpcl", "hpcl"], "Transport", "Fuel", None, ["fuel"]),

    # --- Bills & Utilities ---
    MerchantRule(["jio", "reliance jio"], "Bills & Utilities", "Mobile Recharge", "Jio", ["telecom"]),
    MerchantRule(["airtel"], "Bills & Utilities", "Mobile Recharge", "Airtel", ["telecom"]),
    MerchantRule(["vi", "vodafone", "idea cellular"], "Bills & Utilities", "Mobile Recharge", "Vi", ["telecom"]),
    MerchantRule(["bsnl"], "Bills & Utilities", "Mobile Recharge", "BSNL", ["telecom"]),
    MerchantRule(["tata sky", "dish tv", "sun direct", "videocon d2h", "d2h"], "Bills & Utilities", "DTH", None, ["dth"]),
    MerchantRule(["bescom", "msedcl", "tneb", "tata power", "adani electricity", "electricity bill"], "Bills & Utilities", "Electricity", None, ["electricity"]),

    # --- Entertainment ---
    MerchantRule(["netflix"], "Entertainment", "Movies & OTT", "Netflix", ["ott"]),
    MerchantRule(["hotstar", "disney+", "disney plus"], "Entertainment", "Movies & OTT", "Hotstar", ["ott"]),
    MerchantRule(["spotify"], "Entertainment", "Movies & OTT", "Spotify", ["music"]),
    MerchantRule(["youtube premium", "youtube music"], "Entertainment", "Movies & OTT", "YouTube Premium", ["ott"]),
    MerchantRule(["amazon prime", "prime video"], "Entertainment", "Movies & OTT", "Amazon Prime", ["ott"]),
    MerchantRule(["sonyliv", "sony liv"], "Entertainment", "Movies & OTT", "SonyLIV", ["ott"]),
    MerchantRule(["zee5"], "Entertainment", "Movies & OTT", "ZEE5", ["ott"]),
    MerchantRule(["bookmyshow"], "Entertainment", "Events & Concerts", "BookMyShow", ["entertainment"]),

    # --- Financial ---
    MerchantRule(["salary", "sal cr", "sal credit"], "Income", "Salary", None, ["income", "salary"], ["raw_description"]),
    MerchantRule(["mutual fund", "mf sip", "sip debit", "nfo"], "Investments", "Mutual Fund SIP", None, ["investment"]),
    MerchantRule(["emi", "loan emi", "home loan", "car loan", "personal loan"], "Finances", "Loan EMI", None, ["emi"]),
    MerchantRule(["insurance", "lic", "hdfc life", "sbi life", "icici pru"], "Finances", "Insurance Premium", None, ["insurance"]),
    MerchantRule(["credit card", "cc payment", "card outstanding"], "Finances", "Credit Card Payment", None, ["credit-card"]),
    MerchantRule(["income tax", "tds payment", "advance tax", "gst payment"], "Finances", "Tax Payment", None, ["tax"]),

    # --- Income ---
    MerchantRule(["opening balance"], "Income", "Opening Balance", None, ["opening-balance"], ["raw_description"]),

    # --- Financial / Investment ---
    MerchantRule(["indmoney", "ind money"], "Financial", "Mutual Fund SIP", "INDmoney", ["investment"]),
    MerchantRule(["nach-mut-dr", "nach mut dr"], "Financial", "Mutual Fund SIP", None, ["investment", "sip"]),
    MerchantRule(["groww"], "Financial", "Mutual Fund SIP", "Groww", ["investment"]),
    MerchantRule(["zerodha", "coin by zerodha"], "Financial", "Mutual Fund SIP", "Zerodha", ["investment"]),
    MerchantRule(["kuvera"], "Financial", "Mutual Fund SIP", "Kuvera", ["investment"]),

    # --- ATM / Transfers ---
    MerchantRule(["atm withdrawal", "atm cash", "atm wd"], "Transfers", "ATM Withdrawal", None, ["cash"]),
    MerchantRule(["neft", "rtgs", "imps"], "Transfers", "Peer Transfer", None, ["transfer"], ["raw_description"]),

    # --- Transport (public) ---
    MerchantRule(["irctctourism"], "Transport", "Public Transport", "IRCTC Tourism", ["transport", "metro"]),
    MerchantRule(["maha mumbai met", "mumbai metro"], "Transport", "Public Transport", "Mumbai Metro", ["transport", "metro"]),
    MerchantRule(["uts- direct", "uts-direct"], "Transport", "Public Transport", "UTS", ["transport", "train"]),

    # --- Health ---
    MerchantRule(["pharmeasy", "netmeds", "1mg", "medplus", "apollo pharmacy"], "Health", "Pharmacy", None, ["health", "pharmacy"]),
    MerchantRule(["apollo hospital", "fortis", "max hospital", "manipal", "aiims"], "Health", "Doctor & Hospital", None, ["health", "hospital"]),

    # --- Personal Care ---
    MerchantRule(["cult.fit", "cult fit", "curefit"], "Personal Care", "Gym & Fitness", "Cult.fit", ["fitness"]),
    MerchantRule(["urban company", "urbanclap"], "Personal Care", "Salon & Spa", "Urban Company", ["personal-care"]),

    # --- Education ---
    MerchantRule(["coursera", "udemy", "udacity", "unacademy", "byju", "upgrad"], "Education", "Online Courses", None, ["education"]),
    MerchantRule(["school fee", "college fee", "tuition fee"], "Education", "Tuition & Fees", None, ["education"]),

    # --- Housing ---
    MerchantRule(["rent payment", "house rent", "rental"], "Housing", "Rent", None, ["rent"], ["raw_description", "upi_note"]),
    MerchantRule(["maintenance", "society charges", "society fee"], "Housing", "Maintenance & Society Charges", None, ["housing"]),
]


DISAMBIGUATION_RULES: list[DisambiguationRule] = [
    # Uber Eats → Food & Dining (overrides the default Uber → Transport rule)
    DisambiguationRule(
        base_patterns=["uber", "ubereats"],
        override_patterns=["eat", "eats", "food", "ubereats"],
        category="Food & Dining", subcategory="Food Delivery",
        merchant="Uber Eats", tags=["food"],
    ),
    # Amazon Prime Video → Entertainment (overrides Amazon → Shopping)
    DisambiguationRule(
        base_patterns=["amazon", "amzn"],
        override_patterns=["prime video", "primevideo", "prime membership"],
        category="Entertainment", subcategory="Movies & OTT",
        merchant="Amazon Prime", tags=["ott"],
    ),
    # AWS → Financial (overrides Amazon → Shopping)
    DisambiguationRule(
        base_patterns=["amazon", "amzn"],
        override_patterns=["aws", "amazon web"],
        category="Financial", subcategory=None,
        merchant="AWS", tags=["business", "cloud"],
    ),
]


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
    upi_note = _extract_upi_note(txn)

    field_values: dict[str, str] = {
        "raw_description": (txn.get("raw_description") or "").lower(),
        "upi_note": upi_note.lower(),
    }

    # Phase 1: disambiguation rules (more specific — checked before MERCHANT_RULES)
    for drule in DISAMBIGUATION_RULES:
        haystack = " ".join(field_values[f] for f in drule.match_fields if f in field_values)
        if any(_pattern_matches(p, haystack) for p in drule.base_patterns):
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
        haystack = " ".join(field_values[f] for f in rule.match_fields if f in field_values)
        for pattern in rule.patterns:
            if _pattern_matches(pattern, haystack):
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
