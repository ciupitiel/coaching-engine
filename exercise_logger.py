# =============================================================================
#  exercise_logger.py — #16: Exerciții & Calorii Arse · AI Parser
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Parsează descrieri naturale de antrenamente în română și estimează kcal arse.
#
#  Formula medicală standard:
#    calories_burned = MET × weight_kg × (duration_min / 60)
#    MET = Metabolic Equivalent of Task (tabel ACSM/Compendium of Physical Activities)
#    Exemplu: alergat 30 min (MET 8.3) × 75 kg = 311 kcal
#
#  Pattern: AI returnează MET + durată + tip → serverul calculează caloriile.
#  Nu avem încredere în calculele AI — recalculăm local cu greutatea reală.
#  Identic cu food_logger.py (AI parsează structurat, server recalculează).
#
#  Funcție publică:
#    parse_exercise_description(groq_client, description, weight_kg) → dict
# =============================================================================

import json
import re


# ─────────────────────────────────────────────────────────────────────────────
#  TIPURI DE EXERCIȚII
# ─────────────────────────────────────────────────────────────────────────────

EXERCISE_TYPES: dict[str, str] = {
    "cardio":        "Cardio",
    "forta":         "Forță",
    "flexibilitate": "Flexibilitate",
    "sport":         "Sport",
    "general":       "General",
}

INTENSITY_LABELS: dict[str, str] = {
    "usor":       "Ușor",
    "moderat":    "Moderat",
    "intens":     "Intens",
    "very_intens": "Foarte intens",
}


# ─────────────────────────────────────────────────────────────────────────────
#  SYSTEM PROMPT — Specialist fitness, baza MET Compendium
#  temperature=0.10 → output JSON extrem de consistent
# ─────────────────────────────────────────────────────────────────────────────

EXERCISE_PARSE_SYSTEM_PROMPT = """Ești un kinetoterapeut specialist în estimarea consumului energetic.
Primești o descriere a unui antrenament sau activitate fizică și returnezi EXCLUSIV JSON VALID.
Zero text suplimentar. Zero explicații. Zero markdown. Doar JSON parsabil.

STRUCTURĂ JSON EXACTĂ — respectă cheile exact:
{
  "exercise_name": "Alergat",
  "exercise_type": "cardio",
  "duration_min": 30,
  "met_value": 8.3,
  "intensity": "moderat",
  "notes": "Ritm estimat ~8 km/h pe baza descrierii"
}

TIPURI DE EXERCIȚII (exercise_type — alege EXACT):
cardio        → alergat, mers, ciclism, înot, aerobics, dans, sărit coarda, eliptic, bandă
forta         → greutăți, flotări, tracțiuni, genuflexiuni, abdomen, CrossFit, kettlebell
flexibilitate → yoga, stretching, pilates, mobilitate
sport         → fotbal, baschet, tenis, volei, box, arte marțiale, handbal
general       → plimbare, activitate casnică, grădinărit, lucru fizic, mers normal

VALORI MET EXACTE — Compendium of Physical Activities (Ainsworth 2011):
Mers lent 3 km/h:           MET 2.5
Mers moderat 4 km/h:        MET 3.5
Mers rapid 5-6 km/h:        MET 4.5
Drumeție (munte/natură):     MET 5.3
Alergat 8 km/h:              MET 8.3
Alergat 10 km/h:             MET 10.0
Alergat 12 km/h:             MET 11.8
Alergat 14+ km/h:            MET 13.0
Sprint / intervale:           MET 15.0
Ciclism ușor <16 km/h:       MET 4.0
Ciclism moderat 19-22 km/h:  MET 8.0
Ciclism rapid 22-26 km/h:    MET 10.0
Ciclism indoor moderat:       MET 7.0
Ciclism indoor intens:        MET 10.5
Înot ușor (bras lent):        MET 5.8
Înot moderat (crawl):         MET 8.3
Înot intens (competitiv):     MET 10.0
Greutăți generale / sala:     MET 5.0
Greutăți intense / powerlifting: MET 6.0
CrossFit / functional training: MET 12.0
HIIT (intervale de intensitate): MET 12.0
Flotări / tracțiuni:          MET 4.0
Genuflexiuni cu greutate:     MET 5.0
Exerciții abdomen / core:     MET 3.8
Yoga (hatha / vinyasa):       MET 2.5
Yoga intensă / ashtanga:      MET 4.0
Pilates:                      MET 3.0
Stretching:                   MET 2.3
Dans moderat:                 MET 5.0
Dans intens (Zumba):          MET 6.5
Aerobics moderat:             MET 7.3
Sărit coarda:                 MET 10.0
Eliptic moderat:              MET 5.0
Bandă alergare moderată:      MET 7.0
Fotbal:                       MET 10.0
Baschet:                      MET 8.0
Tenis simplu:                 MET 7.3
Tenis dublu:                  MET 6.0
Volei:                        MET 4.0
Box (antrenament general):    MET 10.0
Arte marțiale / MMA:          MET 10.3
Canotaj indoor moderat:       MET 8.5
Handbal:                      MET 10.0
Urcat scări rapid:            MET 8.0

INTENSITATE (inferată din MET):
usor       → MET < 4.5 (poți vorbi în propoziții; efort minim)
moderat    → MET 4.5-7.0 (vorbești cu efort; respirație accelerată)
intens     → MET 7.0-10.0 (abia poți vorbi; transpirație intensă)
very_intens → MET > 10.0 (nu poți vorbi; efort maximal)

REGULI CRITICE:
· Dacă durata NU e menționată explicit, estimează realist:
  HIIT/CrossFit=20min · Sprint=10min · Alergat=30min · Sala=60min
  Yoga/Pilates=60min · Mers=45min · Ciclism=45min · Înot=30min
· met_value = float cu O zecimală (8.3, 10.0, 5.0, 12.0 etc.)
· exercise_type = EXACT unul din: cardio / forta / flexibilitate / sport / general
· notes = includ ritmul/distanța/greutatea NUMAI dacă userul le-a menționat
· NU calcula calories în JSON — serverul le calculează: MET × kg × (min / 60)
· Dacă descrierea e complet de neînțeles → exercise_name="Activitate fizică",
  exercise_type="general", duration_min=30, met_value=3.5, intensity="moderat"
"""


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIE PRINCIPALĂ
# ─────────────────────────────────────────────────────────────────────────────

async def parse_exercise_description(
    groq_client,
    description: str,
    weight_kg:   float = 75.0,
) -> dict:
    """
    Parsează o descriere naturală de antrenament și calculează kcal arse.

    Args:
        groq_client  : instanța AsyncGroq din main.py (reutilizată)
        description  : text liber de la user ("am alergat 5km în 30 min")
        weight_kg    : greutatea userului din profil (pentru formula MET × kg × ore)

    Returns:
        Success: {exercise_name, exercise_type, duration_min, met_value,
                  intensity, calories_burned, notes, weight_used_kg}
        Error:   {error: str}
    """
    try:
        r = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": EXERCISE_PARSE_SYSTEM_PROMPT},
                {"role": "user",   "content": f"Analizează antrenamentul: {description}"},
            ],
            temperature=0.10,   # Extrem de scăzut → JSON consistent, zero creativitate
            max_tokens=300,
            top_p=0.9,
        )

        raw    = r.choices[0].message.content.strip()
        parsed = _safe_parse_json(raw)

        if not parsed:
            return {
                "error": (
                    "AI-ul nu a returnat un JSON valid. "
                    "Încearcă o descriere mai clară (ex: 'am alergat 30 min')."
                )
            }

        return _validate_and_calculate(parsed, weight_kg)

    except Exception as e:
        return {"error": f"Eroare la procesare: {str(e)}"}


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITARE INTERNE
# ─────────────────────────────────────────────────────────────────────────────

def _safe_parse_json(text: str) -> dict | None:
    """
    Parsează JSON din răspunsul AI, robust la markdown fences și text extra.
    Trei strategii în ordine: direct, fără fences, extragere bloc {}.
    """
    for attempt in [text, re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()]:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


def _validate_and_calculate(parsed: dict, weight_kg: float) -> dict:
    """
    Normalizează structura JSON de la AI și calculează caloriile local.

    Formula standard ACSM: calories = MET × weight_kg × (duration_min / 60)
    1 MET = 1 kcal/kg/oră (la repaus total → 1 kcal/kg/oră)
    Exemplu: MET 8.3 × 75 kg × 0.5 ore = 311 kcal

    Nu avem încredere că AI-ul a calculat corect —
    identic cu food_logger.py: AI parsează, server calculează.
    """
    # ── exercise_name ─────────────────────────────────────────────────────────
    exercise_name = str(parsed.get("exercise_name", "Activitate fizică")).strip()
    if not exercise_name:
        exercise_name = "Activitate fizică"

    # ── exercise_type ─────────────────────────────────────────────────────────
    exercise_type = parsed.get("exercise_type", "general")
    if exercise_type not in EXERCISE_TYPES:
        exercise_type = "general"

    # ── duration_min ──────────────────────────────────────────────────────────
    try:
        duration_min = int(round(float(parsed.get("duration_min", 30))))
        duration_min = max(1, min(duration_min, 360))   # 1 min – 6 ore
    except (TypeError, ValueError):
        duration_min = 30

    # ── met_value ─────────────────────────────────────────────────────────────
    try:
        met_value = float(parsed.get("met_value", 3.5))
        met_value = round(max(1.0, min(met_value, 20.0)), 1)   # sanity bounds
    except (TypeError, ValueError):
        met_value = 3.5

    # ── intensity ─────────────────────────────────────────────────────────────
    intensity = parsed.get("intensity", "moderat")
    if intensity not in INTENSITY_LABELS:
        # Inferăm din MET dacă AI-ul a returnat o valoare invalidă
        if met_value < 4.5:
            intensity = "usor"
        elif met_value < 7.0:
            intensity = "moderat"
        elif met_value < 10.0:
            intensity = "intens"
        else:
            intensity = "very_intens"

    # ── notes ─────────────────────────────────────────────────────────────────
    notes = str(parsed.get("notes", "")).strip()

    # ── Calcul calorii (local, nu de la AI) ──────────────────────────────────
    # Formula: calories = MET × weight_kg × (duration_min / 60)
    # Exemplu: 8.3 MET × 75 kg × (30 min / 60) = 311 kcal
    # max(1, ...) → niciodată 0 kcal (chiar și stretching arde ceva)
    calories_burned = round(met_value * weight_kg * (duration_min / 60))
    calories_burned = max(1, calories_burned)

    return {
        "exercise_name":   exercise_name,
        "exercise_type":   exercise_type,
        "duration_min":    duration_min,
        "met_value":       met_value,
        "intensity":       intensity,
        "calories_burned": calories_burned,
        "notes":           notes,
        "weight_used_kg":  round(weight_kg, 1),  # transparență în UI
    }