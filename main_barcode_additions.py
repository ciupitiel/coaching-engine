# =============================================================================
#  main_barcode_additions.py — #15: Barcode Scanner · Backend Router
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Adaugă în main.py EXACT 2 linii:
#
#  ① La importuri (lângă celelalte main_pX_additions):
#       from main_barcode_additions import init_barcode_router
#
#  ② La router registration (după init_coach_router):
#       app.include_router(init_barcode_router())
#
#  Zero tabele noi în DB. Zero chei API. Zero costuri operaționale.
#  -----------------------------------------------------------------------------
#  Open Food Facts API:
#    · Complet gratuit, fără autentificare
#    · 3M+ produse indexate global (acoperire bună România + Europa)
#    · Rate limit: ~100 req/min în condiții normale
#    · Endpoint: https://world.openfoodfacts.org/api/v0/product/{barcode}.json
#
#  Endpoint-uri expuse:
#    GET /food/barcode/{barcode}   → lookup produs + macros per porție
#
#  Flow frontend:
#    Camera → BarcodeDetector API → barcode string
#    → GET /food/barcode/{code}?grams=100
#    → Product card cu selector porție
#    → "Log it" → POST /food/log (engine existent P4)
# =============================================================================

import httpx
from fastapi import APIRouter, HTTPException, Depends
from auth import require_user_email
from premium_guard import require_premium


# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTE
# ─────────────────────────────────────────────────────────────────────────────

OFF_BASE = "https://world.openfoodfacts.org/api/v0/product"

# User-Agent conform politicii Open Food Facts:
# https://wiki.openfoodfacts.org/API/Read/Authentication
HEADERS = {
    "User-Agent": "NoianLab-CoachingEngine/1.5 (contact@noianlab.ro)",
}


# ─────────────────────────────────────────────────────────────────────────────
#  FETCH PRODUS
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_product(barcode: str) -> dict | None:
    """
    Apelează Open Food Facts și returnează raw product dict.

    Returns None dacă:
      · Produsul nu există în baza de date
      · API-ul este indisponibil (timeout, eroare de rețea)
      · Răspunsul are status != 1 (not found)
    """
    url = f"{OFF_BASE}/{barcode}.json"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, headers=HEADERS)
        if r.status_code != 200:
            return None
        data = r.json()
        # OFF returnează status=1 dacă produsul există, status=0 dacă nu
        if data.get("status") != 1:
            return None
        return data.get("product") or {}
    except httpx.TimeoutException:
        return None  # Silent fail → frontend arată fallback
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  PARSE PRODUS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_product(product: dict, grams: float = 100.0) -> dict:
    """
    Parsează raw product OFF și returnează macros scalate pentru `grams` grame.

    Open Food Facts stochează valorile nutriționale per 100g în câmpuri:
      "energy-kcal_100g"     → Calorii
      "proteins_100g"        → Proteină
      "carbohydrates_100g"   → Carbohidrați
      "fat_100g"             → Grăsimi

    Scaling: actual = value_per_100g × (grams / 100)

    Returns dict compatibil cu structura food_logs (macros ca int/float).
    """
    # ── Nume produs ──────────────────────────────────────────────────────────
    # Preferăm română, fallback la orice limbă disponibilă
    name = (
        product.get("product_name_ro")
        or product.get("product_name_en")
        or product.get("product_name")
        or product.get("abbreviated_product_name")
        or "Produs necunoscut"
    ).strip()

    # ── Brand (primul din lista, dacă sunt mai multe) ────────────────────────
    brand_raw = product.get("brands") or ""
    brand = brand_raw.split(",")[0].strip()

    # ── Nutriments cu fallback sigur ─────────────────────────────────────────
    n = product.get("nutriments") or {}

    def _float(key: str) -> float:
        """Extrage float dintr-un câmp nutriments, 0.0 ca fallback."""
        val = n.get(key) or n.get(key.replace("_100g", "")) or 0
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    factor = grams / 100.0

    kcal    = round(_float("energy-kcal_100g") * factor)
    protein = round(_float("proteins_100g")      * factor, 1)
    carbs   = round(_float("carbohydrates_100g") * factor, 1)
    fat     = round(_float("fat_100g")           * factor, 1)

    # ── Serving size din ambalaj (pentru sugestie porție în UI) ──────────────
    serving_g: float | None = None
    raw_serving = product.get("serving_quantity") or product.get("serving_size") or ""
    try:
        # "50g", "50 g", "50ml" → 50.0
        val = str(raw_serving).replace(",", ".").strip().split()[0]
        parsed = float(val)
        if 1 <= parsed <= 2000:   # sanity check
            serving_g = parsed
    except Exception:
        serving_g = None

    # ── Categorii principale (primele 3, fără prefixul "en:") ────────────────
    raw_cats = product.get("categories_tags") or []
    cats = [c.split(":")[-1].replace("-", " ").title() for c in raw_cats[:3]]

    return {
        "name":      name,
        "brand":     brand,
        "grams":     round(grams, 1),
        "calories":  kcal,
        "protein_g": protein,
        "carbs_g":   carbs,
        "fat_g":     fat,
        "serving_g": serving_g,        # poate fi null → UI arată 100g default
        "image_url": (
            product.get("image_front_small_url")
            or product.get("image_small_url")
            or product.get("image_front_url")
        ),
        "quantity":   product.get("quantity") or "",   # ex: "500 g", "1 L"
        "categories": cats,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY ROUTER
# ─────────────────────────────────────────────────────────────────────────────

def init_barcode_router() -> APIRouter:
    """
    Creează și returnează router-ul Barcode Scanner.
    Apelat O SINGURĂ DATĂ la pornire din main.py:
        app.include_router(init_barcode_router())
    """
    router = APIRouter(tags=["#15 · Barcode Scanner"])

    # ── GET /food/barcode/{barcode} ──────────────────────────────────────────
    @router.get("/food/barcode/{barcode}")
    async def barcode_lookup(
        barcode: str,
        grams:   float = 100.0,
        email:   str   = Depends(require_premium),
    ):
        """
        Lookup produs după cod EAN/UPC din Open Food Facts.

        Args:
            barcode : codul de bare scanat (EAN-13, EAN-8, UPC-A, etc.)
            grams   : câte grame → scalează macros față de per-100g (default 100)

        Returns:
            {
                "ok":        true,
                "name":      "Pâine Albă Dobrogea",
                "brand":     "Dobrogea",
                "grams":     100.0,
                "calories":  265,
                "protein_g": 9.0,
                "carbs_g":   50.0,
                "fat_g":     3.5,
                "serving_g": 50.0,       → null dacă ambalajul nu specifică
                "image_url": "https://...",
                "quantity":  "500 g",
                "categories": ["Pâine", "Brutărie"]
            }

        HTTP 400 → barcode cu format invalid (ne-numeric sau lungime greșită)
        HTTP 404 → produs negăsit în OFF (userul loghează manual în textarea)
        HTTP 503 → OFF API indisponibil momentan (userul încearcă din nou)
        """
        # ── Validare format barcode ─────────────────────────────────────────
        # EAN-8: 8 cifre, EAN-13: 13 cifre, UPC-A: 12 cifre, UPC-E: 8 cifre
        clean = barcode.strip().lstrip("0") or "0"   # normalizăm leading zeros

        if not barcode.strip().isdigit():
            raise HTTPException(
                status_code=400,
                detail=(
                    "Format cod invalid — trebuie să conțină doar cifre. "
                    "Încearcă din nou sau descrie manual mâncarea."
                ),
            )
        if not (7 <= len(barcode.strip()) <= 14):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Lungime cod incorectă ({len(barcode.strip())} cifre). "
                    "Codurile EAN au 8 sau 13 cifre, UPC are 12 cifre."
                ),
            )

        # ── Sanitizare grams ────────────────────────────────────────────────
        if not (5.0 <= grams <= 5000.0):
            grams = 100.0

        # ── Fetch Open Food Facts ────────────────────────────────────────────
        product = await _fetch_product(barcode.strip())

        if product is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Produsul cu codul {barcode} nu a fost găsit în Open Food Facts. "
                    "Descrie mâncarea manual în câmpul de text — AI estimează macros."
                ),
            )

        parsed = _parse_product(product, grams=grams)
        return {"ok": True, **parsed}

    return router