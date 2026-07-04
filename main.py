import os
import re
import json
import logging
import base64
import requests
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="EatIQ Receipt Nutrition Proxy", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request schemas ───────────────────────────────────────────────────────────
class ParseReceiptTextRequest(BaseModel):
    raw_ocr_text: str

class ParseReceiptImageRequest(BaseModel):
    image_base64: str
    image_mime_type: str = "image/jpeg"

# ══════════════════════════════════════════════════════════════════════════════
# SUPERMARKET PREFIX DECODER
# Covers UK, European and international chains
# ══════════════════════════════════════════════════════════════════════════════
STORE_PREFIXES = {
    # Sainsbury's
    "JS":    "Sainsbury's",
    "TTD":   "Taste the Difference",
    "OEP":   "Organic",
    "SSTC":  "Sainsbury's So Tasty",
    "SO ORG":"Sainsbury's Organic",
    # Tesco
    "TF":    "Tesco Finest",
    "TE":    "Tesco",
    "TOS":   "Tesco Organic",
    "TESCO": "Tesco",
    # Waitrose
    "WR":    "Waitrose",
    "ESSE":  "Essential Waitrose",
    "WTRSE": "Waitrose",
    # M&S
    "MS":    "M&S",
    "MAS":   "M&S",
    # Morrisons
    "MO":    "Morrisons",
    "MORR":  "Morrisons",
    # Lidl
    "LID":   "Lidl",
    # Aldi
    "ALD":   "Aldi",
    # Co-op
    "CO":    "Co-op",
    # Asda
    "ASDA":  "Asda",
    "AGF":   "Asda Good & Balanced",
    # Iceland
    "ICE":   "Iceland",
}

# Brand abbreviations seen across UK receipts
BRAND_MAP = {
    "WALK":    "Walkers",
    "WALKER":  "Walkers",
    "WALKER B":"Walkers Baked",
    "ROWN":    "Rowntrees",
    "ROWNTREE":"Rowntrees",
    "CAD":     "Cadbury",
    "CADBURY": "Cadbury",
    "MR KIP":  "Mr Kipling",
    "P.E":     "Pizza Express",
    "PE":      "Pizza Express",
    "MAGGI":   "Maggi",
    "SOLERO":  "Solero",
    "CORNETTO":"Cornetto",
    "YV":      "Yeo Valley",
    "HEIN":    "Heinz",
    "KLG":     "Kelloggs",
    "KELLOG":  "Kelloggs",
    "NESCAF":  "Nescafe",
    "NESTLE":  "Nestle",
    "ALPRO":   "Alpro",
    "OATLY":   "Oatly",
    "NAKD":    "Nakd",
    "RXBAR":   "RXBar",
    "LURPAK":  "Lurpak",
    "ANCHOR":  "Anchor",
    "PHILLY":  "Philadelphia",
    "PRINGLE": "Pringles",
    "TYRRELL": "Tyrrells",
    "KETTLE":  "Kettle",
    "INNOCENT":"Innocent",
    "TROPICA": "Tropicana",
    "OJ":      "Orange Juice",
    "RBULL":   "Red Bull",
    "DRTPEP":  "Dr Pepper",
    "COKE":    "Coca-Cola",
    "PEPSI":   "Pepsi",
    "FANTA":   "Fanta",
    "SPRITE":  "Sprite",
    # European brands
    "BARILLA": "Barilla",
    "BONDUELLE":"Bonduelle",
    "DANONE":  "Danone",
    "ACTIVIA": "Activia",
    "ACTIMEL": "Actimel",
    "FLORA":   "Flora",
    "BERTOLLI":"Bertolli",
}

# Lines to always strip — across all supermarkets globally
STRIP_PATTERNS = [
    r"nectar\s+price\s+saving",
    r"nectar\s+points",
    r"clubcard\s+price",
    r"clubcard\s+saving",
    r"myhermes",
    r"balance\s+due",
    r"total\s+due",
    r"amount\s+due",
    r"subtotal",
    r"^total$",
    r"visa\s*(debit)?",
    r"mastercard",
    r"contactless",
    r"cash",
    r"change\s+due",
    r"\[icc\]",
    r"^aid:",
    r"pan\s+sequence",
    r"merchant",
    r"terminal",
    r"auth\s+code",
    r"smartshop",
    r"smart\s+shop",
    r"scan\s+&\s+go",
    r"self\s+scan",
    r"vat\s+number",
    r"vat\s+reg",
    r"www\.",
    r"thank\s+you",
    r"receipt\s+no",
    r"transaction",
    r"cashier",
    r"store\s+manager",
    r"carrefour\s+loyalty",
    r"bonuspunten",   # Dutch
    r"punkte",        # German
    r"points\s+gagnes", # French
    r"^\*+$",
    r"^-+$",
    r"^\d+\s+items?\s+purchased",
]

# Food category classification rules
CATEGORY_RULES = {
    "vegetables": ["veg", "salad", "lettuce", "spinach", "kale", "broccoli", "carrot",
                   "onion", "tomato", "toms", "pepper", "courgette", "aubergine",
                   "mushroom", "celery", "leek", "cabbage", "cauliflower", "asparagus",
                   "bean", "pea", "corn", "sweetcorn", "parsnip", "beetroot", "radish",
                   "cucumber", "garlic", "ginger", "chilli", "lime", "lemon", "herbs",
                   "coriander", "basil", "parsley", "mint", "dill"],
    "fruit":      ["apple", "banana", "orange", "grape", "strawberr", "raspberr",
                   "blueberr", "mango", "pineapple", "melon", "watermelon", "peach",
                   "plum", "cherry", "kiwi", "avocado", "fruit", "berries"],
    "meat":       ["chicken", "beef", "lamb", "pork", "turkey", "bacon", "sausage",
                   "mince", "steak", "chop", "rib", "ham", "salami", "chorizo",
                   "pancetta", "prosciutto", "duck", "venison", "meat"],
    "fish":       ["salmon", "tuna", "cod", "haddock", "prawn", "shrimp", "crab",
                   "lobster", "mackerel", "sardine", "trout", "sea bass", "fish",
                   "seafood", "mussel", "oyster", "squid", "anchovy"],
    "dairy":      ["milk", "mlk", "cheese", "chse", "cheddar", "yoghurt", "yog",
                   "butter", "cream", "crem", "mozzarella", "brie", "camembert",
                   "parmesan", "feta", "halloumi", "eggs", "egg", "custard",
                   "quark", "fromage", "creme fraiche"],
    "bread":      ["bread", "brd", "loaf", "roll", "bun", "bagel", "pitta", "naan",
                   "wrap", "tortilla", "croissant", "muffin", "scone", "flatbread",
                   "fbread", "sourdough", "baguette", "ciabatta", "focaccia"],
    "cereals":    ["cereal", "porridge", "oat", "granola", "muesli", "cornflake",
                   "weetabix", "shreddies", "rice krispie", "bran", "wheat",
                   "rice", "pasta", "noodle", "quinoa", "couscous", "lentil"],
    "snacks":     ["crisp", "crsp", "chip", "popcorn", "pretzel", "nut", "peanut",
                   "almond", "cashew", "walnut", "pistachio", "trail mix",
                   "rice cake", "oatcake", "cracker", "biscuit", "digestive"],
    "desserts":   ["cake", "tart", "pie", "pudding", "ice cream", "gelato", "sorbet",
                   "doughball", "donut", "brownie", "cookie", "biscuit", "chocolate",
                   "choc", "twirl", "kitkat", "mars", "snickers", "wispa",
                   "cornetto", "solero", "pastil", "sweets", "candy", "jelly",
                   "mr kip", "kipling", "cadbury", "cad ", "milka"],
    "alcohol":    ["beer", "lager", "ale", "wine", "spirit", "vodka", "gin",
                   "whisky", "whiskey", "rum", "brandy", "prosecco", "champagne",
                   "cider", "stout", "porter", "craft beer", "corona", "heineken",
                   "stella", "peroni", "budweiser", "strongbow"],
    "soft_drinks":["juice", "jce", "smoothie", "water", "sparkling", "fizzy",
                   "cola", "coke", "pepsi", "fanta", "sprite", "lemonade",
                   "squash", "cordial", "tea", "coffee", "cocoa", "hot chocolate",
                   "innocent", "tropicana", "oasis", "ribena", "lucozade",
                   "red bull", "rbull", "monster", "energy drink"],
    "condiments": ["sauce", "ketchup", "mayo", "mustard", "vinegar", "dressing",
                   "salsa", "pesto", "tapenade", "chutney", "pickle", "relish",
                   "soy sauce", "teriyaki", "hot sauce", "tabasco", "worcester",
                   "oil", "seasoning", "spice", "salt", "pepper", "stock", "gravy"],
    "ready_meals":["ready meal", "meal kit", "pizza", "lasagne", "curry", "stir fry",
                   "soup", "sandwich", "wrap", "sushi", "dim sum", "spring roll",
                   "masala", "tikka", "korma", "biryani", "pad thai", "ramen",
                   "burger", "hot dog", "nugget", "fish finger", "pie"],
    "frozen":     ["frozen", "freeze", "ice cream", "sorbet", "gelato", "chips",
                   "fries", "waffle", "pancake", "fish finger", "nugget"],
}

def classify_category(name: str) -> str:
    name_lower = name.lower()
    for category, keywords in CATEGORY_RULES.items():
        for kw in keywords:
            if kw in name_lower:
                return category
    return "other"

def decode_abbreviations(raw: str) -> str:
    """Expand known store prefixes and brand abbreviations."""
    text = raw.strip()
    # Try store prefix first (e.g. JS, TTD, OEP)
    for prefix, expansion in STORE_PREFIXES.items():
        pattern = re.compile(r'^\*?' + re.escape(prefix) + r'\s+', re.IGNORECASE)
        if pattern.match(text):
            text = pattern.sub('', text).strip()
            text = f"{expansion} {text}"
            break
    # Try brand prefix
    for abbr, brand in BRAND_MAP.items():
        pattern = re.compile(r'^\*?' + re.escape(abbr) + r'\s*', re.IGNORECASE)
        if pattern.match(text):
            text = pattern.sub('', text).strip()
            text = f"{brand} {text}"
            break
    # Remove leading asterisk (promotional marker on Sainsbury's receipts)
    text = re.sub(r'^\*', '', text).strip()
    return text

def extract_weight(name: str) -> tuple:
    """Extract weight/volume/quantity from item name. Returns (clean_name, weight)."""
    # Patterns: 500G, 3.408L, 260ML, X6, X4, 6PK, 16PK
    weight_pattern = re.compile(
        r'\s*(\d+\.?\d*\s*(?:KG|G|ML|L|LTR|OZ|LB)|\d+\.?\d*L|\bX\d+\b|\d+\s*PK\b|\d+\s*PACK\b)',
        re.IGNORECASE
    )
    weights = weight_pattern.findall(name)
    clean_name = weight_pattern.sub('', name).strip()
    clean_name = re.sub(r'\s+', ' ', clean_name).strip()
    weight = ', '.join(w.strip() for w in weights) if weights else None
    return clean_name, weight

def should_strip(line: str) -> bool:
    """Return True if this line should be removed from the receipt."""
    line_lower = line.lower().strip()
    if not line_lower:
        return True
    for pattern in STRIP_PATTERNS:
        if re.search(pattern, line_lower):
            return True
    # Strip lines that are only numbers, symbols, or card data
    if re.match(r'^[\*\d\s\-\=\#]+$', line_lower):
        return True
    return False

def extract_price(line: str) -> Optional[float]:
    """Extract price from a receipt line. Returns None if no price found."""
    # Match £X.XX or -£X.XX (discounts return None)
    match = re.search(r'(?<!\-)\£(\d+\.\d{2})', line)
    if match:
        return float(match.group(1))
    # Also handle formats without £ symbol: 2.99 at end of line
    match = re.search(r'\s(\d{1,3}\.\d{2})\s*$', line)
    if match:
        return float(match.group(1))
    return None

def parse_receipt_lines(raw_text: str) -> List[Dict]:
    """
    Pure Python receipt parser — works across all supermarkets.
    Strips noise, decodes abbreviations, extracts price and weight.
    """
    lines = raw_text.split('\n')
    items = []
    
    # Find where items start (after store header)
    # Skip header lines until we hit a line with a price
    header_done = False
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Skip obvious header lines
        if not header_done:
            if re.search(r'\£\d+\.\d{2}', line):
                header_done = True
            else:
                continue
        
        # Skip strip lines
        if should_strip(line):
            continue
            
        # Extract price
        price = extract_price(line)
        
        # Skip lines with no price (usually sub-headers or noise)
        if price is None:
            continue
            
        # Skip discount lines (negative prices)
        if re.search(r'-\£\d+\.\d{2}', line):
            continue
            
        # Extract the item name (remove price from line)
        name_raw = re.sub(r'\£\d+\.\d{2}', '', line).strip()
        name_raw = re.sub(r'\s+', ' ', name_raw).strip()
        
        # Skip if name is empty after cleaning
        if not name_raw:
            continue
            
        # Decode abbreviations
        name_decoded = decode_abbreviations(name_raw)
        
        # Extract weight/quantity
        name_clean, weight = extract_weight(name_decoded)
        
        # Classify category
        category = classify_category(name_clean)
        
        # Confidence: high if we recognised a prefix or brand, low otherwise
        confidence = "high" if name_decoded != name_raw else "low"
        
        items.append({
            "raw_name":   name_raw,
            "clean_name": name_clean,
            "weight":     weight,
            "price":      price,
            "category":   category,
            "confidence": confidence,
        })
    
    return items

# ══════════════════════════════════════════════════════════════════════════════
# AI VISION LAYER — GPT-4o Mini reads the image
# ══════════════════════════════════════════════════════════════════════════════

VISION_PROMPT = """You are reading a supermarket receipt image for the EatIQ health app.

Your ONLY job is to extract the raw text from this receipt exactly as printed.
Do NOT interpret, translate or expand abbreviations — copy them exactly.
Do NOT add any text that is not on the receipt.

Return a JSON object with this exact structure:
{
  "store": "store name from receipt header",
  "date": "date if visible, or empty string",
  "raw_lines": [
    "WALKER B CHS&ON X6                £2.20",
    "Nectar Price Saving               -£0.45",
    "JS SOFT TACOS WRAPS               £1.25"
  ]
}

Include every line exactly as printed including prices, discounts, and totals.
The downstream parser will handle filtering — include everything.
Return ONLY valid JSON, no markdown, no explanation."""

class Processor:
    def __init__(self):
        self.ai  = OpenAI()
        self.off_base = "https://search.openfoodfacts.org/search"
        self.off_headers = {"User-Agent": "EatIQ/2.0 (https://eatiq.app)"}

    def extract_text_from_image(self, b64: str, mime: str) -> Dict:
        """Use GPT-4o Mini vision to extract raw text from receipt image."""
        logger.info("Extracting text from receipt image")
        data_url = f"data:{mime};base64,{b64}"
        response = self.ai.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,  # Zero temperature for exact extraction
            max_tokens=2000,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": VISION_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                                "detail": "low"
                            }
                        },
                        {
                            "type": "text",
                            "text": "Extract all text from this receipt exactly as printed."
                        }
                    ]
                }
            ]
        )
        raw = response.choices[0].message.content
        return json.loads(raw)

    def fetch_nutrition(self, search_term: str, weight_str: str = None) -> Dict:
        """
        Look up nutrition from Open Food Facts Search-a-licious API.
        The legacy text_search endpoint (cgi/search.pl) returns 503 globally.
        This uses the new Elasticsearch-powered search.openfoodfacts.org endpoint.
        """
        try:
            params = {
                "q":              search_term,
                "langs":          "en",
                "page_size":      1,
                "fields":         "product_name,brands,nutriments",
            }
            resp = requests.get(
                self.off_base,
                params=params,
                headers=self.off_headers,
                timeout=5
            )
            if resp.status_code != 200:
                logger.warning(f"OFF search returned {resp.status_code} for '{search_term}'")
                return {}

            data = resp.json()
            hits = data.get("hits", [])
            if not hits:
                return {}

            best = hits[0]
            n = best.get("nutriments", {})

            nutrition = {
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

            # Add per-serving calculation if we have weight
            serving_g = self._parse_weight_grams(weight_str)
            if serving_g:
                factor = serving_g / 100
                nutrition["serving_g"]           = serving_g
                nutrition["calories_per_serving"] = round(nutrition["calories_100g"] * factor, 0)
                nutrition["protein_per_serving"]  = round(nutrition["macronutrients_per_100g"]["proteins_g"] * factor, 1)
                nutrition["fat_per_serving"]      = round(nutrition["macronutrients_per_100g"]["fats_g"] * factor, 1)
                nutrition["carbs_per_serving"]    = round(nutrition["macronutrients_per_100g"]["carbohydrates_g"] * factor, 1)

            return nutrition

        except requests.Timeout:
            logger.warning(f"OFF timeout for '{search_term}'")
            return {}
        except Exception as e:
            logger.error(f"OFF error for '{search_term}': {e}")
            return {}

    def _parse_weight_grams(self, weight_str: str) -> Optional[float]:
        """Convert weight string to grams."""
        if not weight_str:
            return None
        w = weight_str.upper().replace(' ', '')
        m = re.match(r'(\d+\.?\d*)(KG|G|ML|L|LTR|OZ|LB)', w)
        if not m:
            return None
        val, unit = float(m.group(1)), m.group(2)
        conversions = {'G': 1, 'KG': 1000, 'ML': 1, 'L': 1000,
                       'LTR': 1000, 'OZ': 28.35, 'LB': 453.6}
        return val * conversions.get(unit, 1)

    def process_image(self, b64: str, mime: str) -> Dict:
        """Full pipeline: image → text → parse → enrich."""
        # Step 1: Extract raw text via AI vision
        extracted = self.extract_text_from_image(b64, mime)
        store = extracted.get("store", "Supermarket")
        date  = extracted.get("date", "")
        raw_lines = extracted.get("raw_lines", [])
        raw_text  = '\n'.join(raw_lines)
        
        logger.info(f"Extracted {len(raw_lines)} raw lines from {store}")
        
        # Step 2: Parse with Python parser
        items = parse_receipt_lines(raw_text)
        logger.info(f"Parsed {len(items)} food items after filtering")
        
        # Step 3: Enrich with nutrition data
        enriched = []
        for item in items:
            nutrition = self.fetch_nutrition(item["clean_name"], item.get("weight"))
            enriched.append({
                "receipt_extracted_name":   item["clean_name"],
                "receipt_raw_name":         item["raw_name"],
                "receipt_extracted_weight": item.get("weight"),
                "price":                    item.get("price", 0),
                "category":                 item["category"],
                "confidence":               item["confidence"],
                "store":                    store,
                "date":                     date,
                "database_nutrition":       nutrition,
            })
        
        return {
            "store":    store,
            "date":     date,
            "items":    enriched,
            "summary": {
                "total_items":      len(enriched),
                "high_confidence":  sum(1 for i in enriched if i["confidence"] == "high"),
                "low_confidence":   sum(1 for i in enriched if i["confidence"] == "low"),
                "with_nutrition":   sum(1 for i in enriched if i["database_nutrition"]),
            }
        }

    def process_text(self, raw_text: str) -> Dict:
        """Parse pre-extracted OCR text."""
        items = parse_receipt_lines(raw_text)
        enriched = []
        for item in items:
            nutrition = self.fetch_nutrition(item["clean_name"], item.get("weight"))
            enriched.append({
                "receipt_extracted_name":   item["clean_name"],
                "receipt_raw_name":         item["raw_name"],
                "receipt_extracted_weight": item.get("weight"),
                "price":                    item.get("price", 0),
                "category":                 item["category"],
                "confidence":               item["confidence"],
                "database_nutrition":       nutrition,
            })
        return {
            "store": "",
            "date":  "",
            "items": enriched,
            "summary": {
                "total_items":    len(enriched),
                "high_confidence":sum(1 for i in enriched if i["confidence"] == "high"),
                "low_confidence": sum(1 for i in enriched if i["confidence"] == "low"),
                "with_nutrition": sum(1 for i in enriched if i["database_nutrition"]),
            }
        }


proc = Processor()

# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/")
async def health():
    return {
        "status":  "ok",
        "service": "EatIQ Receipt Proxy",
        "version": "2.0",
        "model":   "gpt-4o-mini",
        "parser":  "smart-supermarket-aware",
        "nutrition_source": "Open Food Facts"
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
        logger.error(f"Text parse error: {e}")
        raise HTTPException(500, str(e))

@app.post("/api/parse-receipt-image")
async def parse_image(body: ParseReceiptImageRequest):
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set.")
    if not body.image_base64:
        raise HTTPException(400, "image_base64 is empty.")
    try:
        return proc.process_image(body.image_base64, body.image_mime_type)
    except Exception as e:
        logger.error(f"Image parse error: {e}")
        raise HTTPException(500, str(e))
