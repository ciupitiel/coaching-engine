# =============================================================================
#  photo_food_analyzer.py — Motor AI Vision · Photo Food Log
#  Noian Lab · v6 — Google Gemini cu auto-detect model disponibil
#  Groq vision: decommissioned Aug 2025 → migrat complet pe Gemini
# =============================================================================

import json
import re
import os
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Candidați în ordinea preferinței — auto-detectăm primul disponibil
_MODEL_CANDIDATES = [
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash-lite-preview-06-17",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-pro-latest",
    "gemini-1.5-pro",
]

# Cache model — detectat o dată la startup, refolosit la fiecare request
_active_model: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
#  PROMPT — optimizat pentru precizie maximă
# ─────────────────────────────────────────────────────────────────────────────
_PROMPT = """Ești nutriționist expert cu 20 de ani experiență. Analizează CU ATENȚIE toate alimentele vizibile în această imagine.

Returnează EXCLUSIV un obiect JSON valid, fără text înainte/după, fără ``` markdown:

{
  "detected": true,
  "meal_name": "Denumire concisă a mesei în română",
  "analysis_quality": "high",
  "foods": [
    {
      "name": "Nume exact aliment în română",
      "portion_estimate": "~200g",
      "calories": 250,
      "protein_g": 20,
      "carbs_g": 30,
      "fat_g": 8,
      "confidence": "high"
    }
  ],
  "total_calories": 250,
  "total_protein_g": 20,
  "total_carbs_g": 30,
  "total_fat_g": 8,
  "notes": "observații relevante despre preparare sau estimare"
}

Dacă NU există NICIUN aliment vizibil în imagine:
{"detected": false, "meal_name": null, "foods": [], "total_calories": 0, "total_protein_g": 0, "total_carbs_g": 0, "total_fat_g": 0, "analysis_quality": "none", "notes": "Niciun aliment detectat"}

═══ REGULI CRITICE ═══
1. detected: true pentru ORICE aliment vizibil (farfurie, bol, pahar cu băutură, fruct, etc.)
2. Identifică FIECARE aliment separat — nu le combina dacă sunt distincte
3. Estimează gramajul din context: dimensiunea farfuriei, lingura, mâna ca referință
4. total_calories = SUMA exactă din foods[]. Calculează aritmetic înainte să răspunzi.
5. confidence: "high"=ești sigur 90%+ | "medium"=estimare rezonabilă | "low"=ghici
6. analysis_quality: "high"=imagine clară | "medium"=parțial obscur | "low"=calitate slabă
7. Dacă vezi mai multe farfurii/boluri, analizează-le pe TOATE

═══ TABEL DE REFERINȚĂ RAPID (per 100g, valorile NETE după gătit) ═══
PROTEINE:
  piept pui fiert=165kcal P31 C0 G4 | piept pui la cuptor=195kcal P29 C0 G8
  pulpă pui=209kcal P26 C0 G12 | carne tocată vită 20%fat=250kcal P17 C0 G20
  somon la cuptor=208kcal P20 C0 G13 | ton conservă=116kcal P26 C0 G1
  ou întreg 60g=78kcal P6.3 C0.4 G5.3 | albușuri=52kcal P11 C0.7 G0.2
  brânză telemea=258kcal P16 C1 G21 | cașcaval=370kcal P25 C1.3 G29

LACTATE:
  iaurt 0% simplu=59kcal P10 C4 G0 | iaurt 2%=63kcal P5 C3.6 G3
  iaurt grecesc 0%=57kcal P10 C3.6 G0.2 | skyr=62kcal P11 C4 G0.2
  lapte integral=61kcal P3.2 C4.8 G3.3 | lapte 1.5%=47kcal P3.4 C5 G1.5

CEREALE & CARBOHIDRAȚI:
  orez alb fiert=130kcal P2.4 C28 G0.3 | orez brun fiert=112kcal P2.6 C23 G0.9
  paste fierte=131kcal P4.8 C25 G1.1 | paste integrale fierte=124kcal P5.3 C23 G1
  pâine albă=265kcal P9 C49 G3.2 | pâine integrală=247kcal P13 C41 G3.4
  fulgi ovăz=389kcal P17 C66 G7 | granola 30g=134kcal P3 C20 G5
  cartofi fierți=77kcal P2 C18 G0.1 | cartofi prăjiți=312kcal P3.4 C41 G15
  piure cartofi cu unt=83kcal P2 C13 G3 | pâine felie 30g=80kcal P2.7 C15 G1

FRUCTE:
  banane=89kcal P1.1 C23 G0.3 | mere=52kcal P0.3 C14 G0.2
  afine=57kcal P0.7 C14 G0.3 | zmeură=32kcal P0.7 C7.3 G0.4
  căpșuni=32kcal P0.7 C7.7 G0.3 | struguri=69kcal P0.7 C18 G0.2
  portocale=47kcal P0.9 C12 G0.1 | mango=60kcal P0.8 C15 G0.4
  avocado=160kcal P2 C9 G15 | kiwi=61kcal P1.1 C15 G0.5

LEGUME:
  broccoli=34kcal P2.8 C6.6 G0.4 | spanac=23kcal P2.9 C3.6 G0.4
  roșii=18kcal P0.9 C3.9 G0.2 | castraveți=16kcal P0.7 C3.6 G0.1
  salată verde=15kcal P1.4 C2.2 G0.2 | morcovi=41kcal P0.9 C10 G0.2
  ardei roșu=31kcal P1 C6 G0.3 | fasole verde fiartă=35kcal P2 C7 G0.4
  mazăre fiartă=84kcal P5.4 C14 G0.4 | porumb fiert=96kcal P3.4 C21 G1.5

GRĂSIMI & ALTELE:
  ulei măsline=884kcal P0 C0 G100 | unt=717kcal P0.9 C0.1 G81
  unt arahide=588kcal P25 C20 G50 | migdale=579kcal P21 C22 G50
  nuci=654kcal P15 C14 G65 | semințe chia=486kcal P17 C42 G31
  miere 15g=46kcal P0 C12.5 G0 | ciocolată neagră=546kcal P5 C60 G31
  smântână 20%=191kcal P2.7 C3.4 G19

PREPARATE ROMÂNEȘTI COMUNE:
  șaormă medie=420kcal P25 C38 G18 | pizza felie=250kcal P11 C30 G10
  supă de pui cu tăiței=45kcal P4 C4.5 G1.2 | ciorbă de burtă=89kcal P6 C7 G4
  mici 1 buc=180kcal P10 C5 G13 | cozonac 50g=185kcal P4 C28 G7"""


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITARE
# ─────────────────────────────────────────────────────────────────────────────

def _strip_data_url(image_data: str) -> str:
    """Extrage base64 pur din data URL."""
    if "," in image_data:
        return image_data.split(",", 1)[1]
    return image_data

def _detect_mime(image_data: str) -> str:
    """Detectează MIME type din data URL."""
    prefix = image_data[:40]
    if "image/png"  in prefix: return "image/png"
    if "image/webp" in prefix: return "image/webp"
    if "image/gif"  in prefix: return "image/gif"
    return "image/jpeg"

def _parse_json(raw: str) -> dict | None:
    """Parsare robustă: strip markdown, extrage primul JSON object."""
    # Strip markdown code blocks
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    # Încearcă direct
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Extrage primul { ... } complet
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
        "analysis_quality": "none",
        "notes": reason or "Niciun aliment detectat în imagine.",
    }

def _validate(data: dict) -> dict:
    """Sanitizează răspunsul AI și recalculează totalele din foods[]."""
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
        try:
            cal = max(0, round(float(item.get("calories", 0) or 0)))
            if cal == 0:
                continue  # Skip alimente fără calorii (eroare de parsing)
            fixed.append({
                "name":             str(item.get("name", "Aliment")).strip(),
                "portion_estimate": str(item.get("portion_estimate", "—")).strip(),
                "calories":         cal,
                "protein_g":        max(0, round(float(item.get("protein_g", 0) or 0))),
                "carbs_g":          max(0, round(float(item.get("carbs_g",   0) or 0))),
                "fat_g":            max(0, round(float(item.get("fat_g",     0) or 0))),
                "confidence":       item.get("confidence", "medium"),
            })
        except (ValueError, TypeError):
            continue

    data["foods"] = fixed

    if fixed:
        # Recalculăm totalele din foods[] — ignorăm ce a spus AI-ul
        data["total_calories"]  = sum(f["calories"]  for f in fixed)
        data["total_protein_g"] = sum(f["protein_g"] for f in fixed)
        data["total_carbs_g"]   = sum(f["carbs_g"]   for f in fixed)
        data["total_fat_g"]     = sum(f["fat_g"]     for f in fixed)
        data["detected"] = True  # Forțăm True dacă avem alimente cu calorii
    else:
        data["detected"] = False

    return data


# ─────────────────────────────────────────────────────────────────────────────
#  AUTO-DETECT MODEL DISPONIBIL
# ─────────────────────────────────────────────────────────────────────────────

async def _find_working_model() -> str | None:
    """
    Testează candidații în ordine și returnează primul care funcționează.
    Rezultatul e cached în _active_model pentru eficiență.
    """
    global _active_model

    if _active_model:
        return _active_model

    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY lipsă din environment variables!")
        return None

    genai.configure(api_key=GEMINI_API_KEY)

    # Încearcă să obținem lista de modele disponibile
    try:
        available_names = {
            m.name.replace("models/", "")
            for m in genai.list_models()
            if "generateContent" in m.supported_generation_methods
        }
        logger.info("Gemini modele disponibile: %s", sorted(available_names))
    except Exception as e:
        logger.warning("Nu s-a putut lista modelele Gemini: %s", e)
        available_names = set()

    # Testăm candidații — prioritizăm pe cei din lista disponibilă
    to_test = []
    for c in _MODEL_CANDIDATES:
        if not available_names or c in available_names:
            to_test.insert(0, c)  # Disponibil explicit → prioritate
        else:
            to_test.append(c)     # Nu știm sigur → testăm oricum

    for candidate in to_test:
        try:
            model = genai.GenerativeModel(candidate)
            resp  = await model.generate_content_async(
                "Răspunde cu exact un cuvânt: OK",
                generation_config=genai.GenerationConfig(max_output_tokens=10),
            )
            if resp.text:
                logger.info("Gemini model activ: %s", candidate)
                _active_model = candidate
                return candidate
        except Exception as e:
            logger.debug("Model %s indisponibil: %s", candidate, str(e)[:100])
            continue

    logger.error("Niciun model Gemini disponibil din lista de candidați!")
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIE PRINCIPALĂ
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_food_photo(groq_client, image_data: str) -> dict:
    """
    Analizează o fotografie cu mâncare via Google Gemini.
    Parametrul groq_client e păstrat pentru compatibilitate cu endpoint-ul existent.

    Returns dict cu:
      detected, meal_name, analysis_quality, foods[], total_calories,
      total_protein_g, total_carbs_g, total_fat_g, notes
    """
    if not GEMINI_API_KEY:
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

    # Auto-detectăm modelul disponibil
    model_name = await _find_working_model()
    if not model_name:
        return _not_detected(
            "Niciun model Gemini disponibil momentan. Verifică cheia API și încearcă din nou."
        )

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(model_name)

        image_part = {
            "inline_data": {
                "data":      b64_clean,
                "mime_type": mime_type,
            }
        }

        response = await model.generate_content_async(
            [_PROMPT, image_part],
            generation_config=genai.GenerationConfig(
                temperature=0.05,       # Ultra-consistent — nutriție nu e creativă
                max_output_tokens=1600,
            ),
        )

        raw = response.text or ""

        logger.info(
            "Photo vision raw (%s): chars=%d | preview: %s",
            model_name, len(raw), raw[:200].replace("\n", " ")
        )

        if not raw.strip():
            logger.warning("Photo vision: răspuns gol de la %s", model_name)
            # Resetăm cache-ul — poate modelul e instabil
            global _active_model
            _active_model = None
            return _not_detected("Modelul nu a returnat un răspuns. Încearcă din nou.")

        parsed = _parse_json(raw)
        if not parsed:
            logger.warning(
                "Photo vision: JSON parse failed (%s) | raw: %s",
                model_name, raw[:400]
            )
            return _not_detected(
                "Nu s-a putut procesa răspunsul AI. "
                "Încearcă o fotografie mai clară sau descrie manual mâncarea."
            )

        result = _validate(parsed)

        logger.info(
            "Photo vision OK (%s): detected=%s | foods=%d | kcal=%s",
            model_name,
            result.get("detected"),
            len(result.get("foods", [])),
            result.get("total_calories", 0),
        )

        return result

    except Exception as exc:
        err_str = str(exc)
        logger.error(
            "Photo vision EXCEPTION (%s): %s: %s",
            model_name, type(exc).__name__, err_str[:300]
        )
        # Dacă modelul nu mai e disponibil, resetăm cache-ul
        if "404" in err_str or "not available" in err_str.lower():
            _active_model = None
            logger.info("Model %s marcat ca indisponibil — va fi re-detectat", model_name)

        return _not_detected(
            "Eroare temporară la analiza imaginii. Încearcă din nou în câteva secunde."
        )


# ─────────────────────────────────────────────────────────────────────────────
#  DEBUG ENDPOINT — /food/photo/debug-vision
# ─────────────────────────────────────────────────────────────────────────────

async def debug_vision_models(groq_client) -> dict:
    """
    Testează conexiunea Gemini și returnează:
    - Modelul activ
    - Lista completă de modele disponibile pe cont
    - Status OK/FAIL
    """
    global _active_model
    _active_model = None  # Resetăm cache pentru test fresh

    if not GEMINI_API_KEY:
        return {
            "working_models": [],
            "recommended":    None,
            "status":         "FAIL",
            "error":          "GEMINI_API_KEY lipsă din Render Environment Variables",
        }

    genai.configure(api_key=GEMINI_API_KEY)

    # Listăm toate modelele disponibile
    try:
        all_models = sorted([
            m.name.replace("models/", "")
            for m in genai.list_models()
            if "generateContent" in m.supported_generation_methods
        ])
    except Exception as e:
        all_models = [f"Eroare la listare: {e}"]

    # Găsim primul model funcțional
    model_name = await _find_working_model()

    if model_name:
        return {
            "working_models":  [model_name],
            "recommended":     model_name,
            "status":          "OK",
            "provider":        "Google Gemini",
            "all_available":   all_models,
        }
    else:
        return {
            "working_models":  [],
            "recommended":     None,
            "status":          "FAIL",
            "error":           "Niciun model disponibil — verifică cheia API sau upgrade la Gemini paid",
            "all_available":   all_models,
            "tried":           _MODEL_CANDIDATES,
        }