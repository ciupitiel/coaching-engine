# =============================================================================
#  photo_food_analyzer.py — Motor AI Vision · Photo Food Log
#  Noian Lab · v5 — Google Gemini 2.0 Flash
#  (Groq vision decommissioned Aug 2025 → migrat pe Gemini)
# =============================================================================

import json
import re
import os
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL   = "gemini-2.0-flash"

# ─────────────────────────────────────────────────────────────────────────────
#  PROMPT
# ─────────────────────────────────────────────────────────────────────────────
_PROMPT = """Ești nutriționist expert. Analizează TOATE alimentele vizibile în imagine și returnează DOAR JSON valid.

Returnează EXCLUSIV acest JSON (fără text înainte/după, fără ```):
{
  "detected": true,
  "meal_name": "Denumire scurtă a mesei",
  "analysis_quality": "high",
  "foods": [
    {
      "name": "Nume aliment în română",
      "portion_estimate": "~200g",
      "calories": 150,
      "protein_g": 10,
      "carbs_g": 15,
      "fat_g": 5,
      "confidence": "high"
    }
  ],
  "total_calories": 150,
  "total_protein_g": 10,
  "total_carbs_g": 15,
  "total_fat_g": 5,
  "notes": "orice observație relevantă"
}

Dacă NU există mâncare în imagine:
{"detected": false, "meal_name": null, "foods": [], "total_calories": 0, "total_protein_g": 0, "total_carbs_g": 0, "total_fat_g": 0, "analysis_quality": "none", "notes": ""}

REGULI CRITICE:
- detected: true dacă există ORICE aliment vizibil
- Estimează TOATE porțiile vizibile, chiar și parțiale
- total_calories = SUMA exactă din foods[]. Verifică aritmetic.
- confidence: "high" sigur | "medium" estimezi | "low" ghicești
- analysis_quality: "high" clară | "medium" parțial | "low" slabă

Referință rapidă (per 100g):
piept pui=165kcal P31 C0 G4 | pui copt=200kcal P26 C0 G11 | piure cartofi=83kcal P2 C17 G1
banane=89kcal P1 C23 G0 | mere=52kcal P0.3 C14 G0 | afine=57kcal P0.7 C14 G0
zmeura=32kcal P0.7 C8 G0 | capsuni=32kcal P0.7 C8 G0 | struguri=69kcal P0.7 C18 G0
orez fiert=130kcal P2.5 C28 G0.3 | paste fierte=131kcal P5 C25 G1 | paine=265kcal P9 C49 G3
iaurt 0%=59kcal P10 C4 G0 | iaurt gras=97kcal P9 C4 G5 | skyr=67kcal P11 C4 G0
branza telemea=258kcal P16 C1 G21 | ou 60g=78kcal P6 C0 G5
salata verde=15kcal P1.4 C2 G0 | rosii=18kcal P0.9 C4 G0 | castraveti=16kcal P0.7 C4 G0
unt arahide=588kcal P25 C20 G50 | granola 30g=134kcal P3 C20 G5 | miere 15g=46kcal P0 C12 G0
nuci=654kcal P15 C14 G65 | migdale=579kcal P21 C22 G50"""


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITARE
# ─────────────────────────────────────────────────────────────────────────────

def _strip_data_url(image_data: str) -> str:
    if "," in image_data:
        return image_data.split(",", 1)[1]
    return image_data

def _detect_mime(image_data: str) -> str:
    if "image/png"  in image_data[:30]: return "image/png"
    if "image/webp" in image_data[:30]: return "image/webp"
    return "image/jpeg"

def _parse_json(raw: str) -> dict | None:
    cleaned = re.sub(r"```(?:json)?", "", raw).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None

def _not_detected(reason: str = "") -> dict:
    return {
        "detected": False, "meal_name": None, "foods": [],
        "total_calories": 0, "total_protein_g": 0,
        "total_carbs_g": 0, "total_fat_g": 0,
        "analysis_quality": "none", "notes": reason,
    }

def _validate(data: dict) -> dict:
    defaults = {
        "detected": False, "meal_name": None, "foods": [],
        "total_calories": 0, "total_protein_g": 0,
        "total_carbs_g": 0, "total_fat_g": 0,
        "analysis_quality": "medium", "notes": "",
    }
    for k, v in defaults.items():
        if k not in data:
            data[k] = v

    fixed = []
    for item in data.get("foods", []):
        cal = max(0, int(float(item.get("calories", 0) or 0)))
        if cal == 0:
            continue
        fixed.append({
            "name":             str(item.get("name", "Aliment necunoscut")),
            "portion_estimate": str(item.get("portion_estimate", "—")),
            "calories":         cal,
            "protein_g":        max(0, int(float(item.get("protein_g", 0) or 0))),
            "carbs_g":          max(0, int(float(item.get("carbs_g",   0) or 0))),
            "fat_g":            max(0, int(float(item.get("fat_g",     0) or 0))),
            "confidence":       item.get("confidence", "medium"),
        })

    data["foods"] = fixed

    if fixed:
        data["total_calories"]  = sum(f["calories"]  for f in fixed)
        data["total_protein_g"] = sum(f["protein_g"] for f in fixed)
        data["total_carbs_g"]   = sum(f["carbs_g"]   for f in fixed)
        data["total_fat_g"]     = sum(f["fat_g"]     for f in fixed)
        data["detected"] = True
    else:
        data["detected"] = False

    return data


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIE PRINCIPALĂ — Gemini 2.0 Flash Vision
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_food_photo(groq_client, image_data: str) -> dict:
    """
    Analizează o fotografie cu mâncare via Google Gemini 2.0 Flash.
    Parametrul groq_client e păstrat pentru compatibilitate cu endpoint-ul existent.
    """
    if not GEMINI_API_KEY:
        logger.error("Photo vision: GEMINI_API_KEY lipsă din environment variables!")
        return _not_detected(
            "Serviciul de analiză vizuală nu este configurat. "
            "Adaugă GEMINI_API_KEY în Render Environment Variables."
        )

    mime_type = _detect_mime(image_data)
    b64_clean = _strip_data_url(image_data)

    logger.info(
        "Photo vision start (Gemini): mime=%s b64_len=%d",
        mime_type, len(b64_clean)
    )

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)

        image_part = {
            "inline_data": {
                "data":      b64_clean,
                "mime_type": mime_type,
            }
        }

        response = await model.generate_content_async(
            [_PROMPT, image_part],
            generation_config=genai.GenerationConfig(
                temperature=0.10,
                max_output_tokens=1400,
            ),
        )

        raw = response.text or ""

        logger.info(
            "Photo vision raw (Gemini): chars=%d | preview: %s",
            len(raw), raw[:200].replace("\n", " ")
        )

        if not raw.strip():
            logger.warning("Photo vision: Gemini răspuns gol")
            return _not_detected("Modelul nu a returnat un răspuns. Încearcă din nou.")

        parsed = _parse_json(raw)
        if not parsed:
            logger.warning("Photo vision: JSON parse failed | raw: %s", raw[:300])
            return _not_detected("Nu s-a putut procesa răspunsul AI. Încearcă din nou.")

        result = _validate(parsed)

        logger.info(
            "Photo vision OK (Gemini): detected=%s | foods=%d | kcal=%s",
            result.get("detected"),
            len(result.get("foods", [])),
            result.get("total_calories", 0),
        )

        return result

    except Exception as exc:
        logger.error(
            "Photo vision EXCEPTION (Gemini): %s: %s",
            type(exc).__name__, str(exc)[:300]
        )
        return _not_detected(
            "Eroare la analiza imaginii. Verifică cheia API și încearcă din nou."
        )


# ─────────────────────────────────────────────────────────────────────────────
#  DEBUG — compatibil cu endpoint-ul existent /food/photo/debug-vision
# ─────────────────────────────────────────────────────────────────────────────

async def debug_vision_models(groq_client) -> dict:
    """
    Testează conexiunea Gemini și returnează statusul.
    """
    if not GEMINI_API_KEY:
        return {
            "working_models": [],
            "recommended": None,
            "status": "FAIL",
            "error": "GEMINI_API_KEY lipsă din Render Environment Variables",
        }

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = await model.generate_content_async("Răspunde cu un singur cuvânt: OK")
        text = response.text or ""
        return {
            "working_models": [GEMINI_MODEL],
            "recommended":    GEMINI_MODEL,
            "status":         "OK",
            "response":       text[:100],
            "provider":       "Google Gemini",
            "note":           "Groq vision decommissioned Aug 2025. Using Gemini 2.0 Flash.",
        }
    except Exception as e:
        return {
            "working_models": [],
            "recommended":    None,
            "status":         "FAIL",
            "error":          f"{type(e).__name__}: {str(e)[:300]}",
        }