# EatIQ Backend Proxy v1.1

FastAPI server bridging the EatIQ app with:
- GPT-4o Mini (OpenAI) — reads receipt images, cleans OCR text, classifies food items
- Open Food Facts — free real per-100g nutritional data, no key needed

## Endpoints
GET  /                         — health check
POST /api/parse-receipt        — send raw OCR text
POST /api/parse-receipt-image  — send base64 image (recommended, used by the app)

## Environment variable required
OPENAI_API_KEY=sk-your-key-here

## Deploy free on Railway (5 minutes)
1. Push this folder to a GitHub repo
2. Go to railway.app → New Project → Deploy from GitHub repo
3. Select your repo
4. Go to Variables tab → add: OPENAI_API_KEY = sk-your-key-here
5. Go to Settings → Networking → Generate Domain
6. Copy the URL (e.g. https://eatiq-backend.up.railway.app)
7. Open EatIQ app → Profile → Backend server URL → paste URL → Save

## Set a spending cap (important)
1. Go to platform.openai.com → Settings → Billing → Usage limits
2. Set monthly budget limit to £15 or $20
3. This ensures you never get a surprise bill

## Cost reference
Model: gpt-4o-mini
~$0.00015 per receipt scan (input) + ~$0.00060 per scan (output)
Total: ~$0.00075 per scan = roughly $0.75 per 1,000 scans
At 10,000 users × 4 scans/month = 40,000 scans = ~$30/month maximum

## Run locally for testing
pip install -r requirements.txt
export OPENAI_API_KEY=sk-your-key-here
uvicorn main:app --reload --port 8000
# Visit http://localhost:8000 to confirm health check
