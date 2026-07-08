import os
import re
import json
import logging
import base64
import io
import requests
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from PIL import Image, ImageEnhance, ImageFilter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="EatIQ Receipt Nutrition Proxy", version="2.4")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ParseReceiptTextRequest(BaseModel):
    raw_ocr_text: str

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
    r"subtotal", r"^total$", r"visa\s*(debit)?", r"mastercard", r"contactless",
    r"cash", r"change\s+due", r"\[icc\]", r"^aid:", r"pan\s+sequence",
    r"merchant", r"terminal", r"auth\s+code", r"smartshop", r"smart\s+shop",
    r"scan\s+&\s+go", r"self\s+scan", r"vat\s+number", r"vat\s+reg",
    r"www\.", r"thank\s+you", r"receipt\s+no", r"transaction", r"cashier",
    r"store\s+manager", r"bonuspunten", r"punkte", r"points\s+gagnes",
    r"^\*+$", r"^-+$", r"^\d+\s+items?\s+purchased",
    r"price\s+saving",   # now handled by extract_discount — kept for non-£ variants
    r"you\s+saved",
]

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
    """Classify with dessert brand check first to avoid misclassification."""
    name_lower = name.lower()
    # Check dessert brands first — these override generic keyword matching
    for brand in DESSERT_BRANDS:
        if brand in name_lower:
            return "desserts"
    # Then check category rules in order
    for category, keywords in CATEGORY_RULES.items():
        for kw in keywords:
            if kw in name_lower:
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
    return False

def decode_abbreviations(raw: str) -> str:
    text = raw.strip()
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
    Two-pass parser with discount handling:
    Pass 1 — collect all non-stripped lines with their prices
    Pass 2 — pair name-only lines with following price-only lines
    Discounts (Nectar savings etc.) are subtracted from the preceding item price.
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

    for line in item_lines:

        # Check for discount line BEFORE stripping
        discount = extract_discount(line)
        if discount is not None:
            # Subtract from the last item's price (what you actually paid)
            if items:
                items[-1]["price"] = max(0.0, round(items[-1]["price"] - discount, 2))
                logger.info(f"Applied saving of £{discount:.2f} to '{items[-1]['clean_name']}'")
            pending_name = None
            continue

        if should_strip(line):
            pending_name = None
            continue

        price = extract_price(line)
        is_discount = bool(re.search(r'-\s*£\d+\.\d{2}', line))

        if is_discount:
            # Generic negative price line — also subtract from last item
            match = re.search(r'-\s*£(\d+\.\d{2})', line)
            if match and items:
                val = float(match.group(1))
                items[-1]["price"] = max(0.0, round(items[-1]["price"] - val, 2))
                logger.info(f"Applied negative price £{val:.2f} to '{items[-1]['clean_name']}'")
            pending_name = None
            continue

        # Remove price from line to get name
        name_raw = re.sub(r'£\d+\.\d{2}', '', line).strip()
        name_raw = re.sub(r'\s+', ' ', name_raw).strip()

        if price is not None and name_raw:
            _add_item(items, name_raw, price)
            pending_name = None

        elif price is not None and not name_raw:
            if pending_name:
                _add_item(items, pending_name, price)
                pending_name = None

        elif price is None and name_raw:
            if len(name_raw) > 2 and not name_raw.isnumeric():
                pending_name = name_raw

    return items

def _add_item(items: List[Dict], name_raw: str, price: float):
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
VISION_PROMPT = """Extract all text from this grocery receipt image for EatIQ.
Output raw lines EXACTLY as printed — no changes, no additions, no reordering.
Item name LEFT, price RIGHT. Any currency (£$€¥₹AED CHF kr RM SR). Decimal: period or comma.
Discount lines: -£0.55 or "Nectar Saving -0,45". Include payment/total lines.
UK abbrevs: JS=Sainsbury's TTD=Taste the Difference VV=Yeo Valley WR=Waitrose TF=Tesco Finest.
Return ONLY this JSON:
{"store":"","date":"","currency":"£","raw_lines":["line1","line2"]}"""

# ── Text-only prompt for same-store re-scans (no image) ───────────────────────
TEXT_PROMPT = """Parse this grocery receipt text for EatIQ.
Extract item lines: name LEFT, price RIGHT. Any currency. Discount lines subtract from prior item.
Return ONLY this JSON:
{"store":"","date":"","currency":"£","raw_lines":["line1","line2"]}"""

# ══════════════════════════════════════════════════════════════════════════════
# CORE PROCESSOR
# ══════════════════════════════════════════════════════════════════════════════
class Processor:
    def __init__(self):
        self.ai = OpenAI()
        self.off_base = "https://search.openfoodfacts.org/search"
        self.off_headers = {"User-Agent": "EatIQ/2.4 (https://eatiq.app)"}
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
            max_tokens=2000,
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
            max_tokens=2000,
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

        result = self._fetch_off(term)
        if not result:
            result = self._fetch_usda(term)

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
            n    = best.get("nutriments", {})
            if not n.get("energy-kcal_100g"):
                return {}  # empty result — let USDA try
            return {
                "source":             "Open Food Facts",
                "full_database_name": best.get("product_name"),
                "brand":              best.get("brands", ""),
                "calories_100g":      round(float(n.get("energy-kcal_100g") or 0), 1),
                "macronutrients_per_100g": {
                    "carbohydrates_g": round(float(n.get("carbohydrates_100g") or 0), 1),
                    "proteins_g":      round(float(n.get("proteins_100g") or 0), 1),
                    "fats_g":          round(float(n.get("fat_100g") or 0), 1),
                },
                "sugar_100g": round(float(n.get("sugars_100g") or 0), 1),
                "fibre_100g": round(float(n.get("fiber_100g") or 0), 1),
                "salt_100g":  round(float(n.get("salt_100g") or 0), 1),
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
                "sugar_100g": round(float(nutrients.get(2000) or 0), 1),
                "fibre_100g": round(float(nutrients.get(1079) or 0), 1),
                "salt_100g":  salt_g,
            }
        except Exception as e:
            logger.error(f"USDA error for '{term}': {e}")
            return {}

    def enrich(self, parsed_items: List[Dict], store: str, date: str) -> List[Dict]:
        out = []
        for item in parsed_items:
            nut = self.fetch_nutrition(item["clean_name"], item.get("weight"))
            out.append({
                "receipt_extracted_name":   item["clean_name"],
                "receipt_raw_name":         item["raw_name"],
                "receipt_extracted_weight": item.get("weight"),
                "price":                    item.get("price", 0),
                "category":                 item["category"],
                "confidence":               item["confidence"],
                "store":                    store,
                "date":                     date,
                "database_nutrition":       nut,
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
        return {
            "store":           store,
            "date":            date,
            "currency":        currency,
            "currency_source": currency_source,
            "raw_text":        raw_text,   # returned so frontend can cache for next scan
            "items":           self.enrich(items, store, date),
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
        "version":             "2.6",
        "optimisations":       "prompt-compressed + item-cache + same-store-rescan + usda-fallback",
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
async def parse_text(body: ParseReceiptTextRequest):
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
async def parse_image(body: ParseReceiptImageRequest):
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
