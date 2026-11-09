"""Built-in merchant knowledge: who a payee is and what spending it is.

Data, not logic: pipeline/rules.py does the matching. Patterns are matched
case-insensitively against the parsed payee name and UPI note (word-bounded),
and patterns of 5+ letters/digits also match a payee name with its spaces and
punctuation removed, by prefix, so 'URBANCOMPANY' hits 'urban company' and a
truncated 'ROPPEN TRANSPOR' hits 'roppen transportation'.

UPI shows the registered legal entity, not the brand, so each merchant lists
both (Rapido is 'Roppen Transportation', Zomato's parent is 'Eternal'). Every
(category, subcategory) must exist in the seeded taxonomy; a test enforces it.

Only unambiguous entities belong here. Eternal Ltd, for example, is Zomato,
Blinkit, and District (dining, groceries, events), so it is left to the user's
own history (learned rules, RAG) rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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
    MerchantRule(["swiggy", "bundl technologies"], "Food & Dining", "Food Delivery", "Swiggy", ["food"]),
    MerchantRule(["zomato"], "Food & Dining", "Food Delivery", "Zomato", ["food"]),
    MerchantRule(["instamart"], "Food & Dining", "Food Delivery", "Swiggy Instamart", ["grocery"]),

    # --- Groceries ---
    MerchantRule(["bigbasket", "big basket"], "Food & Dining", "Groceries", "BigBasket", ["grocery"]),
    MerchantRule(["blinkit", "grofers"], "Food & Dining", "Groceries", "Blinkit", ["grocery"]),
    MerchantRule(["zepto", "kiranakart"], "Food & Dining", "Groceries", "Zepto", ["grocery"]),
    MerchantRule(["licious", "delightful gourmet"], "Food & Dining", "Groceries", "Licious", ["grocery"]),
    MerchantRule(["freshtohome"], "Food & Dining", "Groceries", "FreshToHome", ["grocery"]),
    MerchantRule(["dmart", "d-mart"], "Food & Dining", "Groceries", "DMart", ["grocery"]),

    # --- Restaurants / Cafes ---
    MerchantRule(["starbucks"], "Food & Dining", "Cafe & Snacks", "Starbucks", ["coffee"]),
    MerchantRule(["blue tokai"], "Food & Dining", "Cafe & Snacks", "Blue Tokai", ["coffee"]),
    MerchantRule(["third wave coffee"], "Food & Dining", "Cafe & Snacks", "Third Wave Coffee", ["coffee"]),
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
    MerchantRule(["rapido", "roppen transportation"], "Transport", "Cab & Auto", "Rapido", ["transport"]),
    MerchantRule(["irctc"], "Travel", "Train", "IRCTC", ["travel", "train"]),
    MerchantRule(["indigo", "air india", "spicejet", "akasa", "vistara", "go first"], "Travel", "Flights", None, ["travel", "flight"]),
    MerchantRule(["makemytrip", "mmt"], "Travel", None, "MakeMyTrip", ["travel"]),
    MerchantRule(["goibibo", "ibibo group"], "Travel", None, "Goibibo", ["travel"]),
    MerchantRule(["cleartrip"], "Travel", None, "Cleartrip", ["travel"]),
    MerchantRule(["agoda"], "Travel", "Hotels", "Agoda", ["travel", "hotel"]),
    MerchantRule(["airbnb"], "Travel", "Hotels", "Airbnb", ["travel", "hotel"]),
    MerchantRule(["indian railways"], "Travel", "Train", "Indian Railways", ["travel", "train"]),
    MerchantRule(["redbus"], "Travel", "Bus", "redBus", ["travel", "bus"]),
    MerchantRule(["petrol", "petroleum", "hp petrol", "indian oil", "iocl", "bharat petroleum", "bpcl", "hpcl"], "Transport", "Fuel", None, ["fuel"]),

    # --- Bills & Utilities ---
    MerchantRule(["jio", "reliance jio"], "Bills & Utilities", "Mobile Recharge", "Jio", ["telecom"]),
    MerchantRule(["airtel"], "Bills & Utilities", "Mobile Recharge", "Airtel", ["telecom"]),
    # "vi" alone is too false-positive-prone (matches the standalone word in
    # names/notes even with word boundaries), so require telecom context.
    MerchantRule(["vodafone", "idea cellular", "vodafone idea", "vi recharge"], "Bills & Utilities", "Mobile Recharge", "Vi", ["telecom"]),
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
    MerchantRule(["bookmyshow", "bigtree entertainment"], "Entertainment", "Events & Concerts", "BookMyShow", ["entertainment"]),
    MerchantRule(["pvr inox", "pvr limited", "inox leisure"], "Entertainment", "Movies & OTT", "PVR INOX", ["movies"]),

    # --- Financial ---
    MerchantRule(["salary", "sal cr", "sal credit"], "Income", "Salary", None, ["income", "salary"], ["raw_description"]),
    MerchantRule(["mutual fund", "mf sip", "sip debit", "nfo"], "Investments", "Mutual Fund SIP", None, ["investment"]),
    MerchantRule(["emi", "loan emi", "home loan", "car loan", "personal loan"], "Finances", "Loan EMI", None, ["emi"]),
    MerchantRule(["insurance", "lic", "hdfc life", "sbi life", "icici pru"], "Finances", "Insurance Premium", None, ["insurance"]),
    MerchantRule(["credit card", "cc payment", "card outstanding"], "Finances", "Credit Card Payment", None, ["credit-card"]),
    MerchantRule(["income tax", "tds payment", "advance tax", "gst payment"], "Finances", "Tax Payment", None, ["tax"]),

    # --- Income ---
    # Balance-artifact rows (opening balance / B/F) never reach annotation: ingest
    # folds them into statements.opening_balance. Interest-paid lines are real
    # income; matched here so "…Closing Balance" in the text can't confuse later stages.
    MerchantRule(["int.pd", "interest paid", "interest credit"], "Income", "Interest & Dividends", None, ["interest"], ["raw_description"]),

    # --- Investments (brokers / SIP debits) ---
    MerchantRule(["indmoney", "ind money"], "Investments", "Mutual Fund SIP", "INDmoney", ["investment"]),
    MerchantRule(["nach-mut-dr", "nach mut dr"], "Investments", "Mutual Fund SIP", None, ["investment", "sip"]),
    MerchantRule(["groww"], "Investments", "Mutual Fund SIP", "Groww", ["investment"]),
    MerchantRule(["zerodha", "coin by zerodha"], "Investments", "Mutual Fund SIP", "Zerodha", ["investment"]),
    MerchantRule(["kuvera"], "Investments", "Mutual Fund SIP", "Kuvera", ["investment"]),

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
    MerchantRule(["practo"], "Health", "Doctor & Hospital", "Practo", ["health"]),

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
    # AWS → Finances (overrides Amazon → Shopping; closest fit in the taxonomy)
    DisambiguationRule(
        base_patterns=["amazon", "amzn"],
        override_patterns=["aws", "amazon web"],
        category="Finances", subcategory=None,
        merchant="AWS", tags=["business", "cloud"],
    ),
]
