import os
import re
import json
import logging
import base64
import io
import sys
import requests
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from PIL import Image, ImageEnhance, ImageFilter

# Ensure this file's directory is on sys.path so `import uk_food_db` works
# regardless of the current working directory Railway starts us in.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uk_food_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="EatIQ Receipt Nutrition Proxy", version="2.10")

# CORS: only the real EatIQ frontends may call this API from a browser.
# localhost entries allow local development. Note CORS protects against
# browser-based cross-origin calls; script/curl abuse is handled by the
# AbuseGuard + app-token checks below.
ALLOWED_ORIGINS = [
    "https://anu1-ai.github.io",
    "http://localhost:8000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ParseReceiptTextRequest(BaseModel):
    raw_ocr_text: str


# ══════════════════════════════════════════════════════════════════════════════
# ABUSE GUARD — protects the OpenAI bill before real accounts exist.
# Layers (all in-memory; counters reset on redeploy, which is acceptable —
# they are a cost ceiling, not a billing system):
#   1. App token   — requests must carry X-App-Token matching EATIQ_APP_TOKEN.
#                    Not real auth, but stops naive curl/script abuse cold.
#   2. Per-device  — X-Device-Id (persistent UUID from the PWA): daily + monthly caps.
#   3. Per-IP      — backstop for spoofed/rotating device IDs. Generous, because
#                    mobile carriers CGNAT many users behind one IP.
#   4. Global      — hard daily ceiling across ALL traffic. This is the kill
#                    switch that bounds the worst-case daily OpenAI spend.
#   5. Payload     — max image size + max images/day guard against huge uploads.
# All limits configurable via Railway env vars without a code change.
# ══════════════════════════════════════════════════════════════════════════════
import time as _time
from collections import defaultdict

APP_TOKEN = os.environ.get("EATIQ_APP_TOKEN", "eatiq-pwa-2026")

LIMIT_DEVICE_DAY   = int(os.environ.get("LIMIT_DEVICE_DAY",   "12"))    # scans/device/day
LIMIT_DEVICE_MONTH = int(os.environ.get("LIMIT_DEVICE_MONTH", "60"))    # scans/device/30d (Pro is 45)
LIMIT_IP_DAY       = int(os.environ.get("LIMIT_IP_DAY",       "60"))    # scans/IP/day (CGNAT headroom)
LIMIT_GLOBAL_DAY   = int(os.environ.get("LIMIT_GLOBAL_DAY",   "400"))   # all scans/day (~$2/day ceiling)
MAX_IMAGE_MB       = float(os.environ.get("MAX_IMAGE_MB",     "7"))     # per-image upload cap

class AbuseGuard:
    def __init__(self):
        self._device_day   = defaultdict(int)   # (device, day)   -> count
        self._device_month = defaultdict(int)   # (device, month) -> count
        self._ip_day       = defaultdict(int)   # (ip, day)       -> count
        self._global_day   = defaultdict(int)   # day              -> count
        self._blocked      = 0
        self._last_prune   = 0.0

    @staticmethod
    def _day():   return int(_time.time() // 86400)
    @staticmethod
    def _month(): return int(_time.time() // (86400 * 30))

    def _prune(self):
        """Drop stale keys once an hour so memory stays bounded."""
        now = _time.time()
        if now - self._last_prune < 3600:
            return
        self._last_prune = now
        d, m = self._day(), self._month()
        for store, current in ((self._device_day, d), (self._ip_day, d),
                               (self._global_day, d), (self._device_month, m)):
            stale = [k for k in store
                     if (k if isinstance(k, int) else k[1]) < current]
            for k in stale:
                del store[k]

    def check(self, device_id: str, ip: str) -> Optional[str]:
        """Returns an error message if the request should be rejected, else None."""
        self._prune()
        d, m = self._day(), self._month()

        if self._global_day[d] >= LIMIT_GLOBAL_DAY:
            self._blocked += 1
            logger.warning(f"GLOBAL daily cap hit ({LIMIT_GLOBAL_DAY}) — blocking scan")
            return ("EatIQ is experiencing unusually high demand today. "
                    "Please try again tomorrow.")

        if ip and self._ip_day[(ip, d)] >= LIMIT_IP_DAY:
            self._blocked += 1
            logger.warning(f"IP daily cap hit for {ip[:20]}")
            return "Daily scan limit reached for your network. Please try again tomorrow."

        if device_id:
            if self._device_day[(device_id, d)] >= LIMIT_DEVICE_DAY:
                self._blocked += 1
                return ("Daily scan limit reached on this device. "
                        "Limits reset at midnight UTC.")
            if self._device_month[(device_id, m)] >= LIMIT_DEVICE_MONTH:
                self._blocked += 1
                return ("Monthly scan limit reached on this device. "
                        "Limits reset every 30 days.")
        return None

    def record(self, device_id: str, ip: str):
        d, m = self._day(), self._month()
        self._global_day[d] += 1
        if ip:        self._ip_day[(ip, d)] += 1
        if device_id:
            self._device_day[(device_id, d)]   += 1
            self._device_month[(device_id, m)] += 1

    def stats(self) -> Dict:
        d = self._day()
        return {
            "scans_today":    self._global_day.get(d, 0),
            "global_day_cap": LIMIT_GLOBAL_DAY,
            "blocked_total":  self._blocked,
            "devices_today":  sum(1 for k in self._device_day if k[1] == d),
        }

guard = AbuseGuard()


def _client_ip(request: Request) -> str:
    """Railway sits behind a proxy — the real client IP is the first entry
    in X-Forwarded-For. Fall back to the direct peer address."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def enforce_guard(request: Request, image_b64: str = ""):
    """Run all abuse checks. Raises HTTPException on violation."""
    # Layer 1 — app token
    token = request.headers.get("x-app-token", "")
    if token != APP_TOKEN:
        logger.warning(f"Rejected request with bad/missing app token from {_client_ip(request)[:20]}")
        raise HTTPException(401, "Unauthorized.")

    # Layer 5 — payload size (base64 is ~4/3 of raw bytes)
    if image_b64:
        approx_mb = len(image_b64) * 3 / 4 / (1024 * 1024)
        if approx_mb > MAX_IMAGE_MB:
            raise HTTPException(413, f"Image too large ({approx_mb:.1f}MB). Max {MAX_IMAGE_MB:.0f}MB.")

    # Layers 2-4 — rate limits
    device_id = request.headers.get("x-device-id", "")[:64]
    ip = _client_ip(request)
    err = guard.check(device_id, ip)
    if err:
        raise HTTPException(429, err)
    guard.record(device_id, ip)

class ParseReceiptImageRequest(BaseModel):
    image_base64:      str
    image_mime_type:   str  = "image/jpeg"
    profile_country:   str  = "United Kingdom"
    last_store:        str  = ""   # last scanned store name sent from frontend
    last_receipt_text: str  = ""   # raw text from last scan of same store

# Country → currency fallback map (mirrors frontend)
COUNTRY_CURRENCY = {
    'United Kingdom':'£', 'Ireland':'€', 'France':'€', 'Germany':'€',
    'Spain':'€', 'Italy':'€', 'Portugal':'€', 'Netherlands':'€',
    'Belgium':'€', 'Austria':'€', 'Finland':'€', 'Greece':'€',
    'Switzerland':'CHF', 'Sweden':'kr', 'Norway':'kr', 'Denmark':'kr',
    'Poland':'zł', 'Czech Republic':'Kč', 'Hungary':'Ft',
    'Romania':'lei', 'Turkey':'₺',
    'United Arab Emirates':'AED', 'Saudi Arabia':'SR', 'Qatar':'QR',
    'Kuwait':'KD', 'Bahrain':'BD', 'Oman':'OMR',
    'Jordan':'JD', 'Lebanon':'LL', 'Israel':'₪', 'Egypt':'E£',
    'India':'₹', 'Pakistan':'₨', 'Bangladesh':'৳',
    'Sri Lanka':'Rs', 'Nepal':'₨',
    'Singapore':'S$', 'Malaysia':'RM', 'Indonesia':'Rp',
    'Thailand':'฿', 'Vietnam':'₫', 'Philippines':'₱',
    'Hong Kong':'HK$', 'China':'¥', 'Japan':'¥',
    'South Korea':'₩', 'Taiwan':'NT$',
    'Australia':'A$', 'New Zealand':'NZ$',
    'United States':'$', 'Canada':'C$', 'Mexico':'$',
    'Brazil':'R$', 'Argentina':'$', 'Colombia':'$',
    'Chile':'$', 'Peru':'S/',
    'South Africa':'R', 'Nigeria':'₦', 'Kenya':'KSh',
    'Ghana':'₵', 'Ethiopia':'Br',
}

# ══════════════════════════════════════════════════════════════════════════════
# STORE PREFIX DECODER
# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# STORE PREFIX DECODER — UK, Europe, US, Asia, Australia
# ══════════════════════════════════════════════════════════════════════════════
STORE_PREFIXES = {
    # Sainsbury's (UK)
    "JS": "Sainsbury's", "TTD": "Taste the Difference",
    "OEP": "Organic", "SSTC": "Sainsbury's So Tasty",
    # Tesco (UK/Europe)
    "TF": "Tesco Finest", "TE": "Tesco", "TOS": "Tesco Organic", "TESCO": "Tesco",
    # Waitrose (UK)
    "WR": "Waitrose", "ESSE": "Essential Waitrose",
    # M&S (UK)
    "MS": "M&S", "MAS": "M&S",
    # Morrisons (UK)
    "MO": "Morrisons", "MORR": "Morrisons", "MS2": "Morrisons Savers",
    # Other UK
    "CO": "Co-op", "ASDA": "Asda", "AGF": "Asda Good & Balanced",
    "ICE": "Iceland", "LID": "Lidl", "ALD": "Aldi",
    # Carrefour (France/Europe/Global)
    "CAR": "Carrefour", "CF": "Carrefour",
    # Albert Heijn (Netherlands)
    "AH": "Albert Heijn", "AHB": "Albert Heijn Bio",
    # Jumbo (Netherlands)
    "JMB": "Jumbo",
    # Rewe (Germany/Austria)
    "REWE": "Rewe", "RW": "Rewe",
    # Edeka (Germany)
    "EDK": "Edeka",
    # Penny (Germany/Europe)
    "PNY": "Penny",
    # Migros (Switzerland)
    "MIG": "Migros", "MGR": "Migros",
    # Coop (Switzerland/Italy)
    "CPP": "Coop",
    # Mercadona (Spain)
    "MRC": "Mercadona", "HKT": "Hacendado",  # Mercadona own brand
    # Walmart (US)
    "WM": "Walmart", "GV": "Great Value",  # Walmart own brand
    # Kroger (US)
    "KRG": "Kroger", "KR": "Kroger",
    # Whole Foods (US/UK)
    "WFM": "Whole Foods", "WF": "Whole Foods", "365": "365 by Whole Foods",
    # Trader Joe's (US)
    "TJ": "Trader Joe's",
    # Target (US)
    "TGT": "Target", "GD": "Good & Gather",  # Target own brand
    # Coles (Australia)
    "COL": "Coles", "CLS": "Coles",
    # Woolworths (Australia)
    "WOW": "Woolworths", "WLW": "Woolworths",
    # FairPrice (Singapore)
    "FP": "FairPrice", "FPN": "FairPrice Finest",
    # Giant (Singapore/Malaysia)
    "GNT": "Giant",
    # Lulu (Middle East)
    "LLU": "Lulu Hypermarket",
    # Spinneys (Middle East)
    "SPN": "Spinneys",
}

BRAND_MAP = {
    "WALK": "Walkers", "WALKER": "Walkers", "WALKER B": "Walkers Baked",
    "ROWN": "Rowntrees", "CAD": "Cadbury", "CADBURY": "Cadbury",
    "MR KIP": "Mr Kipling", "P.E": "Pizza Express", "PE": "Pizza Express",
    "MAGGI": "Maggi", "SOLERO": "Solero", "CORNETTO": "Cornetto",
    "YV": "Yeo Valley", "HEIN": "Heinz", "KLG": "Kelloggs",
    "NESCAF": "Nescafe", "NESTLE": "Nestle", "ALPRO": "Alpro", "OATLY": "Oatly",
    "LURPAK": "Lurpak", "ANCHOR": "Anchor", "PHILLY": "Philadelphia",
    "PRINGLE": "Pringles", "TYRRELL": "Tyrrells", "KETTLE": "Kettle",
    "INNOCENT": "Innocent", "TROPICA": "Tropicana", "RBULL": "Red Bull",
    "BARILLA": "Barilla", "DANONE": "Danone", "ACTIVIA": "Activia",
    "ACTIMEL": "Actimel", "FLORA": "Flora", "BERTOLLI": "Bertolli",
    "VV": "Yeo Valley",
}

STRIP_PATTERNS = [
    r"nectar\s+price\s+saving", r"nectar\s+points", r"clubcard\s+price",
    r"clubcard\s+saving", r"balance\s+due", r"total\s+due", r"amount\s+due",
    r"subtotal", r"^total\b", r"visa\s*(debit)?", r"mastercard", r"contactless",
    # Payment/tender lines — word-bounded so food names are safe
    # (\bcash\b does NOT match 'cashew'; \bcard\b does NOT match 'cardamom')
    r"\bcash\b", r"\bcard\b", r"\bchange\b", r"\bdebit\b", r"\bcredit\b",
    r"\bmaestro\b", r"\bamex\b", r"american\s+express", r"apple\s+pay",
    r"google\s+pay", r"gift\s*card", r"\bcashback\b", r"\btender\b",
    r"\beft\b", r"chip\s*&?\s*pin", r"\brounding\b", r"\bbalance\b",
    r"card\s+number", r"\*{4,}", r"\[icc\]", r"^aid:", r"pan\s+sequence",
    r"merchant", r"terminal", r"auth\s+code", r"smartshop", r"smart\s+shop",
    r"scan\s+&\s+go", r"self\s+scan", r"vat\s+number", r"vat\s+reg",
    r"www\.", r"thank\s+you", r"receipt\s+no", r"transaction", r"cashier",
    r"store\s+manager", r"bonuspunten", r"punkte", r"points\s+gagnes",
    r"^\*+$", r"^-+$", r"^\d+\s+items?\s+purchased",
    r"price\s+saving",
    r"you\s+saved",
]

# ── Non-food keywords — items discarded BEFORE any API query ─────────────────
# Stage 1: line patterns (checked in should_strip)
NON_FOOD_LINE_PATTERNS = [
    # Household cleaning
    r"floor\s+wipe", r"surface\s+wipe", r"kitchen\s+wipe", r"antibac",
    r"washing.up\s+liquid", r"washing\s+liquid", r"laundry", r"fabric\s+soft",
    r"bleach", r"disinfect", r"cleaner", r"cleaning\s+spray", r"mop",
    r"sponge", r"scrubber", r"dustpan", r"hoover\s+bag",
    # Household supplies
    r"bin\s+bag", r"bin\s+liner", r"kitchen\s+roll", r"toilet\s+roll",
    r"tissue", r"kitchen\s+foil", r"cling\s+film", r"sandwich\s+bag",
    r"freezer\s+bag", r"zip\s+lock", r"baking\s+parchment", r"greaseproof",
    r"tin\s+foil", r"aluminium\s+foil", r"food\s+bag",
    # Personal care
    r"shampoo", r"conditioner", r"body\s+wash", r"shower\s+gel",
    r"toothpaste", r"toothbrush", r"mouthwash", r"dental\s+floss",
    r"deodorant", r"antiperspirant", r"razor", r"shaving",
    r"moisturis", r"face\s+wash", r"face\s+cream", r"sun\s+cream",
    r"sunscreen", r"lip\s+balm", r"cotton\s+pad", r"cotton\s+wool",
    r"sanitary", r"tampon", r"pad\s+ultra", r"incontinence",
    r"nappy", r"diaper", r"baby\s+wipe", r"wet\s+wipe",
    # Non-grocery
    r"greeting\s+card", r"gift\s+card", r"gift\s+wrap", r"balloon",
    r"magazine", r"newspaper", r"book\b", r"battery", r"batteries",
    r"lightbulb", r"light\s+bulb", r"torch", r"candle\s+holder",
    r"flower", r"plant\b", r"compost", r"potting", r"soil\b",
    r"clothing", r"t-shirt", r"sock\b", r"underwear",
    r"pet\s+food", r"dog\s+food", r"cat\s+food", r"cat\s+litter",
    r"bird\s+seed", r"fish\s+food",
    # Pharmacy / medical
    r"paracetamol", r"ibuprofen", r"vitamin\b", r"supplement",
    r"bandage", r"plaster\b", r"antiseptic",
    # Non-food household items commonly on receipts
    r"delicates\s+wash", r"wool\s+wash", r"stain\s+remover",
    r"dryer\s+sheet", r"ironing", r"descaler", r"limescale",
]

def is_non_food_line(line: str) -> bool:
    """Stage 1: discard non-food lines before parsing."""
    line_lower = line.lower().strip()
    for pattern in NON_FOOD_LINE_PATTERNS:
        if re.search(pattern, line_lower):
            return True
    return False

# Stage 2: item-level non-food keyword check (after parsing, before API query)
NON_FOOD_ITEM_KEYWORDS = [
    # Cleaning
    "wipe", "bleach", "cleaner", "disinfect", "washing up", "laundry",
    "fabric softener", "washing liquid", "washing powder", "mop",
    # Household
    "bin bag", "kitchen roll", "toilet roll", "tissue", "kitchen foil",
    "cling film", "tin foil", "aluminium foil", "baking paper",
    # Personal care
    "shampoo", "conditioner", "body wash", "shower gel", "toothpaste",
    "toothbrush", "deodorant", "razor", "moisturiser", "face wash",
    "sun cream", "sunscreen", "cotton pad", "sanitary", "nappy",
    "baby wipe", "wet wipe", "tampon",
    # Non-grocery
    "greeting card", "gift card", "magazine", "newspaper", "battery",
    "batteries", "lightbulb", "flower", "plant pot", "compost",
    "pet food", "dog food", "cat food", "cat litter", "bird seed",
    # Medical
    "paracetamol", "ibuprofen", "vitamin tablet", "bandage", "plaster",
    # Cleaning items commonly on grocery receipts
    "delicates wash", "stain remover", "descaler", "limescale remover",
    "floor wipe", "surface spray",
]

def is_non_food_item(name: str) -> bool:
    """Stage 2: discard non-food items after parsing, before API query."""
    name_lower = name.lower()
    return any(kw in name_lower for kw in NON_FOOD_ITEM_KEYWORDS)

# ── Category rules — ORDER MATTERS: more specific first ──────────────────────
# Dessert brands checked before generic keywords to avoid misclassification
DESSERT_BRANDS = {
    "cornetto", "solero", "magnum", "haagen", "ben jerry", "walls",
    "mr kipling", "mr kip", "cadbury", "cad ", "rowntrees", "rown",
    "haribo", "milka", "lindt", "ferrero", "kinder", "maltesers",
    "twirl", "wispa", "crunchie", "flake", "bounty", "snickers", "mars ",
    "twix", "kitkat", "kit kat", "aero", "yorkie", "lion bar",
}

CATEGORY_RULES = {
    "alcohol":    ["beer", "lager", "ale", "wine", "spirit", "vodka", "gin",
                   "whisky", "whiskey", "rum", "brandy", "prosecco", "champagne",
                   "cider", "stout", "porter", "corona", "heineken", "stella",
                   "peroni", "budweiser", "strongbow", "rosé", "rose wine"],
    "desserts":   ["ice cream", "gelato", "sorbet", "lolly", "lollies",
                   "doughball", "donut", "doughnut", "brownie", "cookie",
                   "biscuit", "chocolate", "choc bar", "twirl", "kitkat",
                   "mars bar", "snickers", "wispa", "pastil", "sweets",
                   "candy", "jelly babies", "jelly bean", "pick n mix",
                   "fudge", "toffee", "caramel", "meringue", "cheesecake",
                   "profiterole", "eclair", "mochi", "tiramisu", "panna cotta",
                   "cornetto", "solero", "magnum", "mr kipling", "mr kip",
                   "milka", "lindt", "ferrero", "kinder", "haribo"],
    "snacks":     ["crisp", "crsp", "chip", "popcorn", "pretzel",
                   "rice cake", "oatcake", "cracker", "nachos",
                   "pork scratching", "trail mix", "nuts mix", "mixed nuts"],
    "meat":       ["chicken", "beef", "lamb", "pork", "turkey", "bacon",
                   "sausage", "mince", "steak", "chop", "rib", "ham",
                   "salami", "chorizo", "pancetta", "prosciutto", "duck",
                   "venison", "meat", "schnitzel", "kebab"],
    "fish":       ["salmon", "tuna", "cod", "haddock", "prawn", "shrimp",
                   "crab", "lobster", "mackerel", "sardine", "trout",
                   "sea bass", "fish", "seafood", "mussel", "oyster",
                   "squid", "anchovy", "kipper", "smoked fish"],
    "dairy":      ["milk", "mlk", "cheese", "chse", "cheddar", "yoghurt",
                   "yogurt", "yog", "butter", "cream", "crem", "mozzarella",
                   "brie", "camembert", "parmesan", "feta", "halloumi",
                   "eggs", "egg", "custard", "quark", "fromage",
                   "creme fraiche", "soured cream", "clotted"],
    "vegetables": ["broccoli", "brcl", "carrot", "onion", "tomato", "toms",
                   "pepper", "courgette", "aubergine", "mushroom", "celery",
                   "leek", "cabbage", "cauliflower", "asparagus", "sweetcorn",
                   "parsnip", "beetroot", "radish", "cucumber", "spinach",
                   "kale", "lettuce", "salad leaves", "mix leaf", "watercress",
                   "rocket", "chard", "fennel", "artichoke", "pumpkin",
                   "butternut", "squash", "sweet potato", "swede", "turnip",
                   "green bean", "mangetout", "sugar snap", "edamame",
                   "chilli", "garlic", "ginger", "herbs", "coriander",
                   "basil", "parsley", "mint", "dill", "chives", "thyme",
                   "rosemary", "bay leaf", "lemongrass"],
    "fruit":      ["apple", "banana", "orange", "grape", "strawberr",
                   "raspberr", "blueberr", "mango", "pineapple", "melon",
                   "watermelon", "peach", "plum", "cherry", "kiwi",
                   "avocado", "fruit", "berries", "lemon", "lime",
                   "grapefruit", "pomegranate", "fig", "date", "apricot",
                   "nectarine", "clementine", "mandarin", "satsuma", "tangerine"],
    "bread":      ["bread", "brd", "loaf", "roll", "bun", "bagel", "pitta",
                   "naan", "wrap", "tortilla", "croissant", "muffin", "scone",
                   "flatbread", "fbread", "sourdough", "baguette", "ciabatta",
                   "focaccia", "brioche", "chapatti", "roti", "crumpet",
                   "english muffin", "tiger bread"],
    "cereals":    ["cereal", "porridge", "oat", "granola", "muesli",
                   "cornflake", "weetabix", "shreddies", "rice krispie",
                   "bran flake", "wheat biscuit", "rice", "pasta", "noodle",
                   "quinoa", "couscous", "lentil", "chickpea", "kidney bean",
                   "black bean", "butter bean", "flour", "semolina"],
    "soft_drinks":["juice", "jce", "smoothie", "water", "sparkling",
                   "cola", "coke", "pepsi", "fanta", "sprite", "lemonade",
                   "squash", "cordial", "innocent", "tropicana", "oasis",
                   "ribena", "lucozade", "red bull", "rbull", "monster",
                   "energy drink", "kombucha", "coconut water", "almond milk",
                   "oat milk", "soy milk", "sports drink"],
    "beverages":  ["tea", "coffee", "cocoa", "hot chocolate", "herbal tea",
                   "green tea", "black tea", "chai", "espresso", "cappuccino",
                   "latte", "instant coffee", "coffee bean", "coffee ground",
                   "decaf", "matcha", "horlicks", "ovaltine", "milo"],
    "condiments": ["sauce", "ketchup", "mayo", "mustard", "vinegar",
                   "dressing", "salsa", "pesto", "tapenade", "chutney",
                   "pickle", "relish", "soy sauce", "teriyaki", "hot sauce",
                   "tabasco", "worcester", "oil", "seasoning", "spice",
                   "salt", "pepper", "stock", "gravy", "jam", "marmalade",
                   "honey", "syrup", "spread", "marmite", "vegemite",
                   "tahini", "hummus"],
    "ready_meals":["ready meal", "meal kit", "pizza", "lasagne", "curry",
                   "stir fry", "soup", "sandwich", "sushi", "dim sum",
                   "spring roll", "masala", "tikka", "korma", "biryani",
                   "pad thai", "ramen", "burger", "nugget", "fish finger",
                   "shepherd pie", "cottage pie", "mac cheese", "mac n cheese",
                   "pasta bake", "risotto", "paella"],
    "frozen":     ["frozen", "freeze"],
}

def classify_category(name: str) -> str:
    """Classify with dessert brand check first, then word-boundary keyword
    matching. Word-boundary matching prevents false hits like 'ale' catching
    'kale', 'sale', 'cravendale', or 'oat' catching 'oat drink' (which is a
    beverage). Keywords longer than one word are checked as substrings for
    convenience (e.g., 'ice cream'), but single-word keywords must match a
    whole word in the name."""
    name_lower = name.lower()
    for brand in DESSERT_BRANDS:
        if brand in name_lower:
            return "desserts"
    for category, keywords in CATEGORY_RULES.items():
        for kw in keywords:
            if " " in kw:
                if kw in name_lower:
                    return category
            else:
                if re.search(r'\b' + re.escape(kw) + r'\b', name_lower):
                    return category
    return "other"

def should_strip(line: str) -> bool:
    line_lower = line.lower().strip()
    if not line_lower:
        return True
    for pattern in STRIP_PATTERNS:
        if re.search(pattern, line_lower):
            return True
    if re.match(r'^[\*\d\s\-\=\#]+$', line_lower):
        return True
    # Stage 1: discard non-food lines before parsing
    if is_non_food_line(line):
        logger.info(f"Non-food line discarded: {line[:60]}")
        return True
    return False

def decode_abbreviations(raw: str) -> str:
    text = raw.strip()
    # Strip barcode/product-code digit runs (6+ consecutive digits) anywhere in
    # the line. Receipts from stores like Lidl/Aldi/M&S print barcodes next to
    # item names; if left in, they pollute nutrition-lookup queries (e.g.
    # "MILK 000000233708" fails the local DB and mismatches on OFF) and make
    # item names unreadable in the UI.
    text = re.sub(r'\b\d{6,}[A-Za-z]?\b', '', text).strip()
    text = re.sub(r'\s{2,}', ' ', text)
    for prefix, expansion in STORE_PREFIXES.items():
        pattern = re.compile(r'^\*?' + re.escape(prefix) + r'\s+', re.IGNORECASE)
        if pattern.match(text):
            text = pattern.sub('', text).strip()
            text = f"{expansion} {text}"
            break
    for abbr, brand in BRAND_MAP.items():
        pattern = re.compile(r'^\*?' + re.escape(abbr) + r'\s*', re.IGNORECASE)
        if pattern.match(text):
            text = pattern.sub('', text).strip()
            text = f"{brand} {text}"
            break
    text = re.sub(r'^\*', '', text).strip()
    return text

def extract_weight(name: str):
    weight_pattern = re.compile(
        r'\s*(\d+\.?\d*\s*(?:KG|G|ML|L|LTR|OZ|LB)|\d+\.?\d*L|\bX\d+\b|\d+\s*PK\b|\d+\s*PACK\b|\d+FL)',
        re.IGNORECASE
    )
    weights = weight_pattern.findall(name)
    clean_name = weight_pattern.sub('', name).strip()
    clean_name = re.sub(r'\s+', ' ', clean_name).strip()
    weight = ', '.join(w.strip() for w in weights) if weights else None
    return clean_name, weight

# Currency symbols and their codes — used for normalisation
CURRENCY_PATTERNS = [
    r'£',           # GBP — UK
    r'\$',          # USD, AUD, CAD, SGD, NZD
    r'€',           # EUR — Eurozone
    r'¥',           # JPY, CNY
    r'₹',           # INR
    r'AED',         # UAE Dirham
    r'CHF',         # Swiss Franc
    r'kr\.?',       # SEK, DKK, NOK
    r'RM',          # Malaysian Ringgit
    r'SR',          # Saudi Riyal
]
CURRENCY_RE = '|'.join(CURRENCY_PATTERNS)

def normalise_decimal(value_str: str) -> float:
    """
    Handle both decimal formats:
    - Anglo: 1.49  (period = decimal)
    - European: 1,49 (comma = decimal)
    Distinguishes 1.234 (thousand separator) from 1.49 (decimal).
    """
    s = value_str.strip()
    # If both comma and period present: 1,234.56 or 1.234,56
    if ',' in s and '.' in s:
        if s.rfind('.') > s.rfind(','):
            # Period is decimal: 1,234.56
            return float(s.replace(',', ''))
        else:
            # Comma is decimal: 1.234,56
            return float(s.replace('.', '').replace(',', '.'))
    elif ',' in s:
        # Could be European decimal (1,49) or thousand sep (1,234)
        parts = s.split(',')
        if len(parts) == 2 and len(parts[1]) == 2:
            return float(s.replace(',', '.'))  # European decimal
        return float(s.replace(',', ''))
    else:
        return float(s)

def extract_price(line: str) -> Optional[float]:
    """
    Extract price from a receipt line — supports £ $ € ¥ ₹ AED CHF kr RM SR.
    Handles both X.XX (Anglo) and X,XX (European) decimal formats.
    Returns None for discount/negative lines.
    """
    # Reject lines that are clearly discounts
    if re.search(r'-\s*(?:' + CURRENCY_RE + r')\s*\d', line):
        return None

    # Try to match: [optional minus] [currency] [digits] [.,] [2 digits]
    # Must NOT be preceded by a minus sign
    pattern = re.compile(
        r'(?<!-)\b(?:' + CURRENCY_RE + r')\s*(\d{1,4}[.,]\d{2})\b',
        re.IGNORECASE
    )
    match = pattern.search(line)
    if match:
        try:
            val = normalise_decimal(match.group(1))
            if 0.01 <= val <= 500.0:
                return val
        except ValueError:
            pass

    # Fallback: bare decimal at end of line (no currency symbol)
    # e.g. "JS WHL MLK    2.40" or "LAIT ENTIER    2,49"
    match = re.search(r'(?<!\d)(\d{1,3}[.,]\d{2})\s*$', line)
    if match:
        try:
            val = normalise_decimal(match.group(1))
            if 0.10 <= val <= 300.0:
                return val
        except ValueError:
            pass

    return None

def is_informational_saving(line: str) -> bool:
    """Summary lines like 'YOU SAVED £2.00' or 'TOTAL SAVINGS £3.50' restate
    discounts that are (almost always) itemised elsewhere on the receipt.
    Subtracting them again would double-count, so they are skipped entirely —
    the total-reconciliation pass catches any receipt where they were the
    only record of the discount."""
    l = line.lower()
    return bool(re.search(r"you\s+saved|total\s+savings?\b|multibuy\s+savings\s*$", l))


def extract_discount(line: str) -> Optional[float]:
    """
    Extract discount value from a saving/promo line across all languages.
    Returns positive float to be subtracted from preceding item price.
    """
    line_lower = line.lower()
    discount_keywords = [
        # English
        r"nectar\s+price\s+saving", r"nectar\s+saving", r"clubcard\s+price",
        r"clubcard\s+saving", r"price\s+saving", r"you\s+saved",
        r"multibuy\s+saving", r"member\s+price", r"loyalty\s+saving",
        r"offer\s+saving", r"promotion", r"discount",
        r"coupon", r"coupo\b", r"mnfctrs", r"voucher", r"meal\s+deal\s+saving",
        r"staff\s+disc", r"price\s+cut", r"rollback",
        # French
        r"remise", r"réduction", r"économie", r"bon\s+de\s+réduction",
        r"carte\s+de\s+fidélité",
        # German
        r"rabatt", r"ersparnis", r"payback", r"treuebon",
        # Dutch
        r"korting", r"bonuspunten", r"appie\s+voordeel",
        # Spanish
        r"descuento", r"ahorro", r"tarjeta\s+club",
        # Arabic
        r"خصم",
    ]
    is_saving = any(re.search(p, line_lower) for p in discount_keywords)
    if not is_saving:
        return None

    # Extract the discount amount — try currency symbol first
    pattern = re.compile(
        r'-?\s*(?:' + CURRENCY_RE + r')\s*(\d{1,4}[.,]\d{2})',
        re.IGNORECASE
    )
    match = pattern.search(line)
    if match:
        try:
            return normalise_decimal(match.group(1))
        except ValueError:
            pass

    # Fallback: bare decimal
    match = re.search(r'-?\s*(\d{1,3}[.,]\d{2})\s*$', line)
    if match:
        try:
            val = normalise_decimal(match.group(1))
            if 0.01 <= val <= 100.0:
                return val
        except ValueError:
            pass

    return None

def parse_receipt_lines(raw_text: str) -> List[Dict]:
    """
    Two-pass parser with layered discount handling:
      1. ADJACENT   — a discount line directly after an item, whose amount is
                      no more than that item's price, is subtracted from THAT
                      item (clear attribution, e.g. 'NECTAR SAVING -£0.50').
      2. POOLED     — discounts that can't be attributed (bill-level coupons,
                      multibuy blocks, amounts exceeding the preceding item)
                      are pooled and spread across all items proportionally.
      3. RECONCILED — if the printed final TOTAL is readable and lower than
                      the sum of item prices, item prices are scaled so they
                      sum to what was actually paid. This is the authoritative
                      correction: it catches coupons the parser missed and
                      prevents double-counting, because it works from the
                      bill's own arithmetic rather than our line reading.
    Informational summaries ('YOU SAVED £2.00') are skipped — they restate
    itemised discounts and would double-count.
    """
    lines = [l.strip() for l in raw_text.split('\n') if l.strip()]

    # Find start of items (first line with a price)
    start_idx = 0
    for i, line in enumerate(lines):
        if re.search(r'£\d+\.\d{2}', line) and not should_strip(line):
            start_idx = i
            break

    item_lines = lines[start_idx:]
    items = []
    pending_name = None
    pooled_discount = 0.0
    receipt_total = None       # last printed TOTAL amount (not savings/subtotal)
    last_was_item = False      # True only when the previous line produced an item

    for line in item_lines:
        # Normalise '£-1.00' / '£ -1.00' (minus AFTER the currency symbol —
        # common OCR reading of coupon lines) to '-£1.00' so all discount
        # regexes see one canonical form.
        line = re.sub(r'(£|\$|€)\s*-\s*(?=\d)', r'-\1', line)
        # Strip single-letter VAT/tax markers printed flush after the price
        # (ASDA: '£2.50D', some tills: '£1.20A'). Without this the price
        # regex's word boundary fails and the whole item is lost.
        line = re.sub(r'(\d[.,]\d{2})\s*[A-Za-z]\s*$', r'\1', line)
        line_lower = line.lower()

        # Informational savings summaries — skip entirely (double-count risk)
        if is_informational_saving(line):
            last_was_item = False
            continue

        # Capture printed TOTAL lines (before should_strip removes them).
        # The LAST total on the receipt wins — receipts often print interim
        # totals before coupons. Excludes 'total savings' via the check above
        # and subtotals here.
        if re.search(r'\btotal\b', line_lower) and 'sub' not in line_lower:
            m = re.search(r'(\d{1,4}[.,]\d{2})\s*$', line)
            if m:
                try:
                    receipt_total = normalise_decimal(m.group(1))
                except ValueError:
                    pass
            last_was_item = False
            continue

        # Discount lines (keyword-based)
        discount = extract_discount(line)
        if discount is not None:
            if last_was_item and items and discount <= items[-1]["price"]:
                items[-1]["price"] = max(0.0, round(items[-1]["price"] - discount, 2))
                logger.info(f"Adjacent saving £{discount:.2f} -> '{items[-1]['clean_name']}'")
            else:
                pooled_discount += discount
                logger.info(f"Pooled saving £{discount:.2f} (no clear item attribution)")
            pending_name = None
            last_was_item = False
            continue

        if should_strip(line):
            pending_name = None
            last_was_item = False
            continue

        price = extract_price(line)
        is_discount = bool(re.search(r'-\s*£\d+\.\d{2}', line))

        if is_discount:
            # Generic negative price line without a recognised keyword
            match = re.search(r'-\s*£(\d+\.\d{2})', line)
            if match:
                val = float(match.group(1))
                if last_was_item and items and val <= items[-1]["price"]:
                    items[-1]["price"] = max(0.0, round(items[-1]["price"] - val, 2))
                    logger.info(f"Adjacent negative £{val:.2f} -> '{items[-1]['clean_name']}'")
                else:
                    pooled_discount += val
                    logger.info(f"Pooled negative £{val:.2f}")
            pending_name = None
            last_was_item = False
            continue

        # Remove price from line to get name
        name_raw = re.sub(r'£\d+\.\d{2}', '', line).strip()
        name_raw = re.sub(r'\s+', ' ', name_raw).strip()

        if price is not None and name_raw:
            _add_item(items, name_raw, price)
            pending_name = None
            last_was_item = True

        elif price is not None and not name_raw:
            if pending_name:
                _add_item(items, pending_name, price)
                pending_name = None
                last_was_item = True
            else:
                last_was_item = False

        elif price is None and name_raw:
            if len(name_raw) > 2 and not name_raw.isnumeric():
                pending_name = name_raw
            last_was_item = False
        else:
            last_was_item = False

    _apply_basket_discounts(items, pooled_discount, receipt_total)
    # Attach receipt-level metadata to the first item so the caller can read
    # the printed total and total discount without changing the return type.
    total_discount = round(pooled_discount, 2)
    printed_total = receipt_total
    if items:
        items[0]["_receipt_total"]   = printed_total
        items[0]["_total_discount"]  = total_discount
    return items


def _apply_basket_discounts(items: List[Dict], pooled: float, receipt_total: Optional[float]):
    """
    Spread unattributed discounts across items proportionally to price, so
    category spend and health-ratio insights reflect what was actually paid.

    Priority: if the printed receipt total is readable and lower than the
    current item sum, use IT as the target (authoritative — covers coupons
    the parser missed AND prevents double-counting). Otherwise fall back to
    subtracting the pooled discount amount.
    """
    if not items:
        return
    current_sum = sum(i["price"] for i in items)
    if current_sum <= 0:
        return

    reduce_by = 0.0
    if receipt_total is not None and 0 <= receipt_total < current_sum - 0.01:
        reduce_by = current_sum - receipt_total
        logger.info(f"Reconciling to printed total £{receipt_total:.2f} "
                    f"(items summed £{current_sum:.2f}, reducing £{reduce_by:.2f})")
    elif pooled > 0:
        reduce_by = min(pooled, current_sum)
        logger.info(f"Distributing pooled discounts £{reduce_by:.2f} proportionally")

    if reduce_by <= 0:
        return

    factor = (current_sum - reduce_by) / current_sum
    for i in items:
        i["price"] = round(i["price"] * factor, 2)
    # Fix rounding drift on the priciest item so the sum matches exactly
    target = round(current_sum - reduce_by, 2)
    drift = round(target - sum(i["price"] for i in items), 2)
    if abs(drift) >= 0.01:
        priciest = max(items, key=lambda x: x["price"])
        priciest["price"] = max(0.0, round(priciest["price"] + drift, 2))

# Names that can never be food items — coupons, vouchers, promo mechanics.
# Final gate in parse_receipt_lines: even if a discount line's price parsed
# in some unforeseen format and it slipped through the discount handlers,
# it is dropped here (its value is recovered by total reconciliation).
DISCOUNT_NAME_RE = re.compile(
    r"coupon|coupo\b|mnfctrs|voucher|multibuy|savings?\b|discount|promo|"
    r"price\s+cut|rollback|meal\s+deal\s+sav|clubcard|nectar|"
    r"remise|réduction|rabatt|korting|descuento",
    re.IGNORECASE
)

# Payment/tender words that can never be food items — final gate mirror of
# the strip patterns, catching lines whose price parsed in unforeseen formats.
PAYMENT_NAME_RE = re.compile(
    r"\bcard\b|\bcash\b|\bchange\b|\btotal\b|\bsubtotal\b|\bvisa\b|"
    r"\bmastercard\b|\bmaestro\b|\bamex\b|\bcontactless\b|\bdebit\b|"
    r"\bcredit\b|\bcashback\b|\btender\b|\bbalance\b|apple\s+pay|"
    r"google\s+pay|gift\s*card|\beft\b|\brounding\b",
    re.IGNORECASE
)

def _add_item(items: List[Dict], name_raw: str, price: float):
    # Hard gate: discount-labelled lines are never items.
    if DISCOUNT_NAME_RE.search(name_raw):
        logger.info(f"Discount-named line blocked from items: '{name_raw[:50]}'")
        return
    # Hard gate: payment/tender lines are never items.
    if PAYMENT_NAME_RE.search(name_raw):
        logger.info(f"Payment-named line blocked from items: '{name_raw[:50]}'")
        return
    name_decoded = decode_abbreviations(name_raw)
    name_clean, weight = extract_weight(name_decoded)
    category = classify_category(name_clean)
    confidence = "high" if name_decoded != name_raw else "low"
    items.append({
        "raw_name":   name_raw,
        "clean_name": name_clean,
        "weight":     weight,
        "price":      price,
        "category":   category,
        "confidence": confidence,
    })

def preprocess_receipt_image(b64: str) -> str:
    """
    Optimise receipt image for OCR before sending to OpenAI.
    - Resize to max 1500px wide (preserves aspect ratio)
    - Convert to greyscale (removes colour noise, reduces tokens)
    - Boost contrast 1.4x (makes faint thermal print sharper)
    - Sharpen edges (improves letter definition)
    - Re-encode as JPEG 85% quality
    Returns new base64 string.
    """
    try:
        raw = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw))

        # Resize: max 1500px wide, preserving aspect ratio
        max_width = 1500
        if img.width > max_width:
            ratio = max_width / img.width
            new_size = (max_width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        # Greyscale — removes colour noise, makes text pop
        img = img.convert('L')

        # Boost contrast
        img = ImageEnhance.Contrast(img).enhance(1.4)

        # Sharpen edges
        img = img.filter(ImageFilter.SHARPEN)

        # Re-encode
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85, optimize=True)
        new_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

        orig_kb = len(b64) * 3 / 4 / 1024
        new_kb  = len(new_b64) * 3 / 4 / 1024
        logger.info(f"Image pre-processed: {orig_kb:.0f}KB → {new_kb:.0f}KB "
                    f"({img.width}×{img.height}px greyscale)")
        return new_b64
    except Exception as e:
        logger.warning(f"Image pre-processing failed, using original: {e}")
        return b64  # fall back to original if anything goes wrong


# ══════════════════════════════════════════════════════════════════════════════
# VISION PROMPT — compressed to ~140 tokens (was ~400)
# Key rules only — GPT-4o Mini is smart enough with minimal instruction
# ══════════════════════════════════════════════════════════════════════════════
VISION_PROMPT = """Receipt OCR. Extract every printed line exactly as shown.
Item=left, price=right. Any currency. Decimals: period or comma.
Return ONLY JSON: {"store":"","date":"","currency":"","raw_lines":["..."]}"""

# ── Text-only prompt for same-store re-scans (no image) ───────────────────────
TEXT_PROMPT = """Receipt text. Extract item lines: name left, price right. Any currency.
Return ONLY JSON: {"store":"","date":"","currency":"","raw_lines":["..."]}"""

# ══════════════════════════════════════════════════════════════════════════════
# CORE PROCESSOR
# ══════════════════════════════════════════════════════════════════════════════
def sanitise_nutrition(result: Optional[Dict], term: str) -> Optional[Dict]:
    """Reject physically-impossible per-100g nutrition. A value that violates
    basic food chemistry means the lookup matched the wrong product (e.g. a
    sauce returning 160g carbs/100g). Better to show 'no match' than a wrong
    number that pollutes insights. Local-DB hits are trusted and skip this."""
    if not result:
        return result
    if result.get("source") == "EatIQ UK DB":
        return result  # curated values are trusted
    m = result.get("macronutrients_per_100g", {}) or {}
    carbs = m.get("carbohydrates_g", 0) or 0
    prot  = m.get("proteins_g", 0) or 0
    fat   = m.get("fats_g", 0) or 0
    kcal  = result.get("calories_100g", 0) or 0
    sugar = result.get("sugar_100g", 0) or 0
    fibre = result.get("fibre_100g", 0) or 0
    salt  = result.get("salt_100g", 0) or 0
    # Per-100g physical ceilings
    impossible = (
        carbs > 100 or prot > 100 or fat > 100 or sugar > 100 or
        fibre > 100 or salt > 100 or kcal > 900 or
        # Macro mass can't exceed 100g/100g in total (small tolerance for
        # rounding and water/ash not counted)
        (carbs + prot + fat) > 105 or
        # Sugar can't exceed total carbs; fibre can't exceed total carbs
        (sugar > carbs + 1) or (fibre > carbs + 1)
    )
    if impossible:
        logger.warning(f"Rejected impossible nutrition for '{term}': "
                       f"kcal={kcal} C={carbs} P={prot} F={fat} "
                       f"sugar={sugar} fibre={fibre} salt={salt} "
                       f"(source: {result.get('source')})")
        return {}   # treated as no-match downstream
    return result


class Processor:
    def __init__(self):
        self.ai = OpenAI()
        self.off_base = "https://search.openfoodfacts.org/search"
        self.off_headers = {"User-Agent": "EatIQ/2.9 (https://eatiq.app)"}
        # Nutrition cache — in-memory, resets on redeploy
        self._nutrition_cache: Dict[str, Dict] = {}
        self._cache_hits   = 0
        self._cache_misses = 0

    def _cache_key(self, term: str) -> str:
        return re.sub(r'\s+', ' ', term.lower().strip())

    def extract_text_from_image(self, b64: str, mime: str) -> Dict:
        logger.info("Pre-processing receipt image")
        b64  = preprocess_receipt_image(b64)
        mime = "image/jpeg"
        logger.info("Sending to GPT-4o Mini for text extraction")
        data_url = f"data:{mime};base64,{b64}"
        response = self.ai.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,
            max_tokens=800,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": VISION_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": data_url, "detail": "auto"}},
                    {"type": "text",
                     "text": "Extract all text from this receipt exactly as printed."}
                ]}
            ]
        )
        return json.loads(response.choices[0].message.content)

    def extract_text_from_text(self, raw_text: str, store: str = "") -> Dict:
        """
        Trick 3: Same-store text re-scan.
        When the user scans the same store again, we already have the raw
        receipt structure. Send text only — ~10x cheaper than vision call.
        """
        logger.info(f"Text-only re-scan for '{store}' — skipping image vision call")
        prompt = f"Store: {store}\n\n{raw_text}"
        response = self.ai.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,
            max_tokens=800,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": TEXT_PROMPT},
                {"role": "user",   "content": prompt}
            ]
        )
        return json.loads(response.choices[0].message.content)

    def fetch_nutrition(self, term: str, weight_str: str = None) -> Dict:
        key = self._cache_key(term)
        # Cache hit — no network call
        if key in self._nutrition_cache:
            self._cache_hits += 1
            logger.info(f"Cache HIT [{self._cache_hits}/{self._cache_hits+self._cache_misses}]: '{term}'")
            return self._nutrition_cache[key]
        # Cache miss — try Open Food Facts first, USDA as fallback
        self._cache_misses += 1
        logger.info(f"Cache MISS [{self._cache_misses} misses]: '{term}'")

        # Defensive: strip any barcode digit-runs that slipped through so the
        # lookup query is clean regardless of which path produced the name.
        term = re.sub(r'\b\d{6,}[A-Za-z]?\b', '', term).strip()
        # Stage 1: local UK food database (instant, zero network).
        result = uk_food_db.match(term)
        if result:
            logger.info(f"Local DB HIT: '{term}' -> {result['full_database_name']}")
        else:
            # Stage 2: Open Food Facts (with sanity check to reject wrong top-hits).
            result = self._fetch_off(term)
            if not result:
                # Stage 3: USDA fallback.
                result = self._fetch_usda(term)

        result = sanitise_nutrition(result, term)
        self._nutrition_cache[key] = result
        return result

    def _fetch_off(self, term: str) -> Dict:
        """Open Food Facts — primary nutrition source."""
        try:
            params = {"q": term, "langs": "en", "page_size": 1,
                      "fields": "product_name,brands,nutriments"}
            resp = requests.get(self.off_base, params=params,
                                headers=self.off_headers, timeout=5)
            if resp.status_code != 200:
                return {}
            hits = resp.json().get("hits", [])
            if not hits:
                return {}
            best = hits[0]
            off_name = best.get("product_name") or ""
            # Sanity check: reject if OFF top hit shares no meaningful token
            # with our query — prevents "LIME" -> "Lime pickle" style errors.
            if not uk_food_db.sanity_check_off(term, off_name):
                logger.info(f"OFF hit rejected by sanity check: '{term}' vs '{off_name}'")
                return {}
            n = best.get("nutriments", {})
            if not n.get("energy-kcal_100g"):
                return {}  # empty result — let USDA try
            return {
                "source":             "Open Food Facts",
                "full_database_name": off_name,
                "brand":              best.get("brands", ""),
                "calories_100g":      round(float(n.get("energy-kcal_100g") or 0), 1),
                "macronutrients_per_100g": {
                    "carbohydrates_g": round(float(n.get("carbohydrates_100g") or 0), 1),
                    "proteins_g":      round(float(n.get("proteins_100g") or 0), 1),
                    "fats_g":          round(float(n.get("fat_100g") or 0), 1),
                },
                "sugar_100g":     round(float(n.get("sugars_100g") or 0), 1),
                "sat_fat_100g":   round(float(n.get("saturated-fat_100g") or 0), 1),
                "fibre_100g":     round(float(n.get("fiber_100g") or 0), 1),
                "salt_100g":      round(float(n.get("salt_100g") or 0), 1),
            }
        except Exception as e:
            logger.warning(f"OFF error for '{term}': {e}")
            return {}

    def _fetch_usda(self, term: str) -> Dict:
        """
        USDA FoodData Central — fallback when OFF returns nothing.
        Especially useful for Indian foods, US brands, and raw ingredients.
        API key read from USDA_API_KEY environment variable.
        Free forever: https://fdc.nal.usda.gov/api-guide.html
        """
        api_key = os.environ.get("USDA_API_KEY", "")
        if not api_key:
            logger.info("USDA_API_KEY not set — skipping USDA fallback")
            return {}
        try:
            url    = "https://api.nal.usda.gov/fdc/v1/foods/search"
            params = {
                "query":    term,
                "api_key":  api_key,
                "pageSize": 1,
                "dataType": "SR Legacy,Branded,Foundation",
            }
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code != 200:
                logger.warning(f"USDA returned {resp.status_code} for '{term}'")
                return {}
            foods = resp.json().get("foods", [])
            if not foods:
                return {}
            food = foods[0]
            # Map USDA nutrient IDs to names
            # 1008=Energy(kcal) 1003=Protein 1004=Fat 1005=Carbs 2000=Sugars
            # 1079=Fibre 1093=Sodium(mg→salt g)
            nutrients = {n["nutrientId"]: n.get("value", 0)
                         for n in food.get("foodNutrients", [])}
            kcal   = round(float(nutrients.get(1008) or 0), 1)
            if kcal == 0:
                return {}
            sodium_mg = float(nutrients.get(1093) or 0)
            salt_g    = round(sodium_mg * 2.5 / 1000, 2)  # Na × 2.5 → NaCl
            return {
                "source":             "USDA FoodData Central",
                "full_database_name": food.get("description", term),
                "brand":              food.get("brandOwner", ""),
                "calories_100g":      kcal,
                "macronutrients_per_100g": {
                    "carbohydrates_g": round(float(nutrients.get(1005) or 0), 1),
                    "proteins_g":      round(float(nutrients.get(1003) or 0), 1),
                    "fats_g":          round(float(nutrients.get(1004) or 0), 1),
                },
                "sugar_100g":   round(float(nutrients.get(2000) or 0), 1),
                "sat_fat_100g": round(float(nutrients.get(1258) or 0), 1),
                "fibre_100g":   round(float(nutrients.get(1079) or 0), 1),
                "salt_100g":    salt_g,
            }
        except Exception as e:
            logger.error(f"USDA error for '{term}': {e}")
            return {}

    def enrich(self, parsed_items: List[Dict], store: str, date: str) -> List[Dict]:
        out = []
        for item in parsed_items:
            name = item["clean_name"]
            # Stage 2: discard non-food items before any API query
            if is_non_food_item(name):
                logger.info(f"Non-food item discarded (no API call): {name}")
                continue  # skip entirely — not shown to user
            nut = self.fetch_nutrition(name, item.get("weight"))
            has_nut = bool(nut and nut.get("calories_100g"))
            # When the local DB has an entry, trust its category over the
            # classifier's guess — the curated hint is more accurate than
            # keyword matching (e.g. "kale" -> vegetables, not alcohol).
            final_category = item["category"]
            if nut and nut.get("category_hint"):
                final_category = nut["category_hint"]
            # internal metadata keys are read at response assembly, not exposed per-item
            out.append({
                "receipt_extracted_name":   name,
                "receipt_raw_name":         item["raw_name"],
                "receipt_extracted_weight": item.get("weight"),
                "price":                    item.get("price", 0),
                "category":                 final_category,
                "confidence":               item["confidence"],
                "store":                    store,
                "date":                     date,
                "database_nutrition":       nut,
                "nutrition_status":         "matched" if has_nut else "not_found",
                "nutrition_source":         (nut or {}).get("source", ""),
            })
        return out

    def process_image(self, b64: str, mime: str,
                      profile_country: str = "United Kingdom",
                      last_store: str = "",
                      last_receipt_text: str = "") -> Dict:

        # ── Trick 3: Same-store text re-scan ──────────────────────────────────
        # If the user is scanning the same store as last time AND we have prior
        # raw text, use the cheaper text-only GPT call instead of vision.
        # Same-store = store names match (case-insensitive, stripped).
        # We still need the image for the current scan's raw text, so we only
        # skip vision when the PRIOR scan text is available as a structure hint.
        # Note: current scan always uses vision — text re-scan applies to the
        # nutrition enrichment pass using cached prior structure as context.
        # Full text re-scan (skipping image entirely) requires the frontend to
        # send the raw OCR text of the current receipt — not yet implemented.
        # For now, same-store detection logs savings opportunity.
        same_store = (
            last_store and
            last_store.lower().strip() not in ("", "supermarket", "store") and
            last_receipt_text.strip()
        )
        if same_store:
            logger.info(f"Same-store detected: '{last_store}' — text re-scan eligible")
            # Use text-only call with prior receipt as context hint
            try:
                extracted = self.extract_text_from_text(last_receipt_text, last_store)
                logger.info("Text re-scan succeeded — vision call skipped")
            except Exception as e:
                logger.warning(f"Text re-scan failed ({e}), falling back to vision")
                extracted = self.extract_text_from_image(b64, mime)
        else:
            extracted = self.extract_text_from_image(b64, mime)

        store    = extracted.get("store", "Supermarket") or last_store or "Supermarket"
        date     = extracted.get("date", "")
        currency = extracted.get("currency", "").strip()
        if not currency or currency.lower() in ("", "unknown", "none"):
            currency        = COUNTRY_CURRENCY.get(profile_country, "£")
            currency_source = f"profile ({profile_country})"
        else:
            currency_source = "receipt"
        logger.info(f"Currency: {currency} (from {currency_source})")
        raw_lines = extracted.get("raw_lines", [])
        raw_text  = '\n'.join(raw_lines)
        logger.info(f"Extracted {len(raw_lines)} raw lines from {store}")
        items = parse_receipt_lines(raw_text)
        logger.info(f"Parsed {len(items)} food items")
        # Read receipt-level metadata attached by parse_receipt_lines
        receipt_total  = items[0].get("_receipt_total")  if items else None
        total_discount = items[0].get("_total_discount", 0.0) if items else 0.0
        enriched = self.enrich(items, store, date)
        item_sum = round(sum(i.get("price", 0) for i in enriched), 2)
        # Discrepancy detection: item_sum has already been reconciled toward
        # receipt_total by _apply_basket_discounts. If it STILL falls short of
        # what was paid, OCR missed one or more lines (their value couldn't be
        # distributed because those items don't exist in our list).
        missing_value = None
        if receipt_total is not None and receipt_total > item_sum + 0.05:
            missing_value = round(receipt_total - item_sum, 2)
        # total_source tells the frontend how to present spend:
        #   'receipt' — printed total was read; it is authoritative
        #   'items'   — no total on the photo; spend = sum of captured items
        total_source = "receipt" if receipt_total is not None else "items"
        effective_total = receipt_total if receipt_total is not None else item_sum
        return {
            "store":           store,
            "date":            date,
            "currency":        currency,
            "currency_source": currency_source,
            "raw_text":        raw_text,   # returned so frontend can cache for next scan
            "items":           enriched,
            "receipt_total":   receipt_total,     # printed total, or null if not on photo
            "item_sum":        item_sum,          # sum of captured item prices
            "effective_total": effective_total,   # the figure to use for spend
            "total_source":    total_source,      # 'receipt' or 'items'
            "total_discount":  total_discount,    # promotions/coupons applied
            "missing_value":   missing_value,     # unaccounted spend (only when total_source=receipt)
            "summary": {
                "total_items":     len(items),
                "high_confidence": sum(1 for i in items if i["confidence"]=="high"),
                "low_confidence":  sum(1 for i in items if i["confidence"]=="low"),
                "cache_hits":      self._cache_hits,
                "cache_misses":    self._cache_misses,
                "text_rescan":     same_store,
            }
        }

    def process_text(self, raw_text: str) -> Dict:
        items = parse_receipt_lines(raw_text)
        return {
            "store": "", "date": "",
            "items": self.enrich(items, "", ""),
            "summary": {"total_items": len(items)}
        }

proc = Processor()

@app.get("/")
async def health():
    total = proc._cache_hits + proc._cache_misses
    hit_rate = round((proc._cache_hits / total) * 100) if total > 0 else 0
    return {
        "status":              "ok",
        "service":             "EatIQ Receipt Proxy",
        "version":             "2.16",
        "optimisations":       "local-uk-db + item-cache + off-sanity-check + usda-fallback + non-food-filter",
        "local_db_stats":      uk_food_db.stats(),
        "abuse_guard":         guard.stats(),
        "usda_configured":     bool(os.environ.get("USDA_API_KEY")),
        "image_detail":        "auto",
        "image_preprocessing": "greyscale+contrast+sharpen",
        "cache": {
            "items_cached": len(proc._nutrition_cache),
            "hits":         proc._cache_hits,
            "misses":       proc._cache_misses,
            "hit_rate_pct": hit_rate,
        }
    }

@app.post("/api/parse-receipt")
async def parse_text(body: ParseReceiptTextRequest, request: Request):
    enforce_guard(request)
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set.")
    if not body.raw_ocr_text.strip():
        raise HTTPException(400, "raw_ocr_text is empty.")
    try:
        return proc.process_text(body.raw_ocr_text)
    except Exception as e:
        logger.error(f"parse-receipt error: {e}")
        raise HTTPException(500, str(e))

@app.post("/api/parse-receipt-image")
async def parse_image(body: ParseReceiptImageRequest, request: Request):
    enforce_guard(request, body.image_base64)
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set.")
    if not body.image_base64:
        raise HTTPException(400, "image_base64 is empty.")
    try:
        return proc.process_image(
            body.image_base64,
            body.image_mime_type,
            body.profile_country,
            body.last_store,
            body.last_receipt_text,
        )
    except Exception as e:
        logger.error(f"parse-receipt-image error: {e}")
        raise HTTPException(500, str(e))
