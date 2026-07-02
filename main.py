import os
import json
import logging
import base64
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
import openfoodfacts

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="EatIQ Receipt Nutrition Proxy", version="1.1")

# ── CORS — allow the EatIQ web app to call this from any origin ───────────────
# In production, replace "*" with your Netlify URL e.g. "https://eatiq.netlify.app"
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

# ── Prompt shared by both endpoints ──────────────────────────────────────────
SYSTEM_PROMPT = """You are an expert UK grocery receipt parser for a health app called EatIQ.

Your job is to extract every food and drink item from the receipt and return structured JSON.

Rules:
1. Identify every distinct food or drink line item — include ALL of them.
2. Remove completely: store codes, routing numbers, promotional text, loyalty points,
   discounts, VAT codes, subtotals, totals, carrier bags, stamps, non-food items.
3. Expand abbreviations where possible:
   e.g. 'TNDR BRCL' -> 'Tenderstem Broccoli'
        'SS MLK 2L' -> 'Semi-Skimmed Milk 2L'
        'CHKN BRST' -> 'Chicken Breast'
        'ORNG JCE'  -> 'Orange Juice'
4. Separate weight or volume into extracted_weight if present on the line item.
5. Classify each item into exactly one category from this list:
   vegetables | fruit | meat | fish | dairy | bread | cereals | snacks |
   desserts | alcohol | soft_drinks | condiments | ready_meals | frozen | other
6. If the receipt image is dark, skewed, or partially visible — do your best
   to read every line. Flag uncertain items with confidence: "low".
7. Return ONLY valid JSON — no markdown fences, no explanation, nothing else.

Required JSON format:
{
  "store": "store name or Unknown",
  "date": "date as shown on receipt or empty string",
  "items": [
    {
      "search_term": "clean product name for Open Food Facts lookup",
      "extracted_weight": "200g or null",
      "category": "vegetables",
      "confidence": "high or low"
    }
  ]
}"""

# ── Core processor ────────────────────────────────────────────────────────────
class Processor:
    def __init__(self):
        # Reads OPENAI_API_KEY from environment automatically
        self.ai = OpenAI()
        self.off = openfoodfacts.API(user_agent="EatIQ/1.1")

    # ── Parse pre-extracted OCR text ─────────────────────────────────────────
    def parse_text(self, ocr_text: str) -> List[Dict[str, Any]]:
        logger.info(f"Parsing OCR text ({len(ocr_text)} chars)")
        try:
            response = self.ai.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": f"Parse this receipt OCR text:\n\"\"\"\n{ocr_text}\n\"\"\""}
                ]
            )
            raw = response.choices[0].message.content
            parsed = json.loads(raw)
            items = parsed.get("items", [])
            logger.info(f"Extracted {len(items)} items from OCR text")
            return self._enrich(items, parsed.get("store",""), parsed.get("date",""))
        except Exception as e:
            logger.error(f"Text parse error: {e}")
            raise

    # ── Parse directly from receipt image ────────────────────────────────────
    def parse_image(self, b64: str, mime_type: str) -> List[Dict[str, Any]]:
        logger.info("Parsing receipt image with GPT-4o Mini vision")
        try:
            # GPT-4o Mini accepts base64 images as data URLs
            data_url = f"data:{mime_type};base64,{b64}"

            response = self.ai.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.1,
                max_tokens=2000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": data_url,
                                    "detail": "high"  # high detail for small receipt text
                                }
                            },
                            {
                                "type": "text",
                                "text": "Parse every food and drink item from this receipt image. "
                                        "The image may be slightly dark or skewed — read carefully. "
                                        "Return the structured JSON as instructed."
                            }
                        ]
                    }
                ]
            )
            raw = response.choices[0].message.content
            parsed = json.loads(raw)
            items = parsed.get("items", [])
            logger.info(f"Extracted {len(items)} items from image")
            return self._enrich(items, parsed.get("store",""), parsed.get("date",""))
        except Exception as e:
            logger.error(f"Image parse error: {e}")
            raise

    # ── Enrich items with Open Food Facts nutrition data ──────────────────────
    def _enrich(self, items: list, store: str, date: str) -> List[Dict[str, Any]]:
        result = []
        for item in items:
            term = item.get("search_term", "")
            nutrition = self._fetch_nutrition(term) if term else {}
            result.append({
                "receipt_extracted_name":   term,
                "receipt_extracted_weight": item.get("extracted_weight"),
                "category":                 item.get("category", "other"),
                "confidence":               item.get("confidence", "high"),
                "store":                    store,
                "date":                     date,
                "database_nutrition":       nutrition,
            })
        return result

    # ── Open Food Facts lookup ────────────────────────────────────────────────
    def _fetch_nutrition(self, search_term: str) -> Dict[str, Any]:
        try:
            results = self.off.product.text_search(query=search_term)
            if not results or not results.get("products"):
                return {}
            best = results["products"][0]
            n = best.get("nutriments", {})
            return {
                "full_database_name": best.get("product_name"),
                "brand":              best.get("brands", "Generic/Unknown"),
                "calories_100g":      n.get("energy-kcal_100g", 0),
                "macronutrients_per_100g": {
                    "carbohydrates_g": n.get("carbohydrates_100g", 0),
                    "proteins_g":      n.get("proteins_100g", 0),
                    "fats_g":          n.get("fat_100g", 0),
                },
                "sugar_100g": n.get("sugars_100g", 0),
                "fibre_100g": n.get("fiber_100g", 0),
                "salt_100g":  n.get("salt_100g", 0),
            }
        except Exception as e:
            logger.error(f"Open Food Facts error for '{search_term}': {e}")
            return {}


# Initialise processor once at startup
proc = Processor()


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/")
async def health():
    return {
        "status": "ok",
        "service": "EatIQ Receipt Proxy",
        "version": "1.1",
        "model": "gpt-4o-mini",
        "nutrition_source": "Open Food Facts"
    }


# ── Endpoint A: OCR text → parse + enrich ────────────────────────────────────
@app.post("/api/parse-receipt")
async def parse_receipt_text(body: ParseReceiptTextRequest):
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set on server.")
    if not body.raw_ocr_text.strip():
        raise HTTPException(400, "raw_ocr_text is empty.")
    try:
        return proc.parse_text(body.raw_ocr_text)
    except Exception as e:
        logger.error(f"parse-receipt error: {e}")
        raise HTTPException(500, f"Parsing failed: {str(e)}")


# ── Endpoint B: base64 image → GPT-4o Mini vision → parse + enrich ───────────
@app.post("/api/parse-receipt-image")
async def parse_receipt_image(body: ParseReceiptImageRequest):
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not set on server.")
    if not body.image_base64:
        raise HTTPException(400, "image_base64 is empty.")
    try:
        return proc.parse_image(body.image_base64, body.image_mime_type)
    except Exception as e:
        logger.error(f"parse-receipt-image error: {e}")
        raise HTTPException(500, f"Image parsing failed: {str(e)}")
