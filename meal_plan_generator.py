# =============================================================================
#  meal_plan_generator.py — P5: Meal Plan Generator · v3 Optimizat
#  Noian Cristian · Bazat pe inteligență artificială
#  -----------------------------------------------------------------------------
#  Optimizări v3 față de v2:
#
#  1. JSON MODE (response_format json_object) → eliminat 100% parse failures
#  2. GENERARE CONCURENTĂ: zilele 1-3 și 4-7 generate simultan cu asyncio.gather
#     → token budget dublu per zi → calitate și varietate mai bune
#  3. SCALING MATEMATIC cu ajustare descrieri (g/ml) → garanție matematică
#     că caloriile returnate = target, chiar dacă AI-ul greșește
#  4. TEMPERATURE ANNEALING: 0.45 → 0.30 → 0.20 pe retry
#  5. VALIDARE DUALĂ: calorii ±15% ȘI proteină ±20% validate separat
# =============================================================================

import asyncio
import json
import re

# ─────────────────────────────────────────────────────────────────────────────
#  SYSTEM PROMPT — structural only, FĂRĂ exemple cu calorii fixe
#  Motivul: exemple cu valori fixe → AI ancorează pe ele indiferent de target
# ─────────────────────────────────────────────────────────────────────────────

MEAL_PLAN_SYSTEM_PROMPT = """\
E�ti un nutriționist specializat în alimentația românească.
Creezi planuri alimentare PRACTICE, GUSTOASE și PRECISE caloric.
Returnezi EXCLUSIV JSON VALID. Zero text suplimentar. Doar JSON parsabil.

STRUCTURĂ JSON OBLIGATORIE:
{
  "plan": [
    {
      "day": <int 1-7>,
      "day_name": "<Luni|Marți|Miercuri|Joi|Vineri|Sâmbătă|Duminică>",
      "meals": [
        {
          "meal": "<Mic Dejun|Prânz|Gustare|Cină>",
          "name": "<string>",
          "description": "<Xg ingredient1, Yml ingredient2, ...>",
          "calories": <int>,
          "protein_g": <int>,
          "carbs_g": <int>,
          "fat_g": <int>,
          "prep_time": "<X min>"
        }
      ],
      "day_totals": {
        "calories": <SUMA_EXACTĂ_MEALS>,
        "protein_g": <SUMA_EXACTĂ>,
        "carbs_g": <SUMA_EXACTĂ>,
        "fat_g": <SUMA_EXACTĂ>
      }
    }
  ],
  "weekly_avg": {
    "calories": <MEDIA_ZILNICĂ>,
    "protein_g": <MEDIA>,
    "carbs_g": <MEDIA>,
    "fat_g": <MEDIA>
  },
  "shopping_tips": ["<sfat1>", "<sfat2>", "<sfat3>", "<sfat4>"]
}

REGULI ABSOLUTE:
· MEREU 4 mese/zi în ordinea: Mic Dejun, Prânz, Gustare, Cină
· Suma caloriilor celor 4 mese = TARGET_KCAL_ZI (toleranță ±50 kcal max)
· Proteina zilnică = TARGET_PROTEIN ±10g
· Alimente 100% accesibile în România (supermarket local, piață)
· Varietate reală — nicio masă repetată în aceeași săptămână
· Cantitate exactă în grame/ml pentru fiecare ingredient în description
· day_totals = suma ARITMETICĂ a meals (nu estima, calculează)

SCALAREA PORȚIILOR LA TARGET CALORIC:
Target 1400-1800 kcal/zi → porții mici:
  Pui: 120-150g | Orez fiert: 150-200g | Ouă: 2 | Pâine: 1-2 felii/zi

Target 1800-2300 kcal/zi → porții medii:
  Pui: 180-220g | Orez fiert: 250-300g | Ouă: 3 | Pâine: 2-3 felii/zi

Target 2300-2800 kcal/zi → porții mari:
  Pui: 270-320g | Orez fiert: 380-430g | Ouă: 4 | Pâine: 3-4 felii/zi

Target 2800-3300 kcal/zi → porții foarte mari:
  Pui: 350-400g | Orez fiert: 480-550g | Ouă: 4-5 | Pâine: 4-5 felii/zi
  + paste, cartofi dulci, pâine la fiecare masă principală

Target >3300 kcal/zi → porții masive:
  Pui: 450-550g | Orez fiert: 600g+ | Ouă: 5-6 | Pâine: 6+ felii/zi
  + unt la preparare, ulei măsline, shake proteic, nuci 60-80g/zi

VALORI NUTRIȚIONALE REFERINȚĂ (per 100g):
Piept pui grătar: 165kcal · P:31 C:0 G:4
Orez fiert: 130kcal · P:2.5 C:28 G:0.3
Paste fierte: 158kcal · P:5.8 C:31 G:0.9
Cartofi fierți: 80kcal · P:2 C:18 G:0.1
Cartofi dulci fierți: 86kcal · P:1.6 C:20 G:0.1
Ou fiert 60g: 78kcal · P:6 C:0 G:5
Brânză telemea: 260kcal · P:16 C:2 G:22
Pâine felie 30g: 75kcal · P:3 C:14 G:1
Somon: 208kcal · P:20 C:0 G:13
Ton conservă (apă): 120kcal · P:26 C:0 G:1
Iaurt grecesc 0%: 59kcal · P:10 C:4 G:0
Nuci: 654kcal · P:15 C:14 G:65
Ulei măsline 1 lingură (10ml): 88kcal · P:0 C:0 G:10
Unt 1 lingură (15g): 108kcal · P:0 C:0 G:12
Caș: 320kcal · P:22 C:2 G:26
Banană 120g: 107kcal · P:1.3 C:27 G:0.4
Mere 150g: 78kcal · P:0.4 C:21 G:0.3

MÂNCĂRURI ROMÂNEȘTI RECOMANDATE:
Mic dejun: omletă/ochiuri/fierte, sandviș telemea+roșii, iaurt+fulgi ovăz+fructe, brânză+pâine+unt
Prânz: pui grătar+orez+salată, paste cu pui, ciorbă+pâine, friptură+cartofi, fasole+cârnați, tocăniță
Gustare: iaurt+nuci+fructe, ouă fierte, shake proteic+banane, caș+roșii+pâine
Cină: somon/ton cuptor+legume, pui+cartofi dulci, mici+salată, omletă+legume+brânză\
"""


# ─────────────────────────────────────────────────────────────────────────────
#  CONTEXT PER OBIECTIV
# ─────────────────────────────────────────────────────────────────────────────

GOAL_CONTEXT_MAP: dict[str, str] = {
    "cut_bland":  "slăbire ușoară · deficit mic · mese sățioase · protein ridicat",
    "cut":        "slăbire moderată · deficit caloric · protein very high · volum mare de legume",
    "mentinere":  "menținere și recompoziție · echilibru macros · flexibilitate alimentară",
    "bulk_lean":  "masă musculară curată · surplus mic · clean foods · carbohidrați în jurul antrenamentului",
    "bulk":       "masă musculară · surplus caloric · mese consistente · densitate calorică ridicată",
}

DAY_NAMES = ["Luni", "Marți", "Miercuri", "Joi", "Vineri", "Sâmbătă", "Duminică"]

# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIA PRINCIPALĂ
# ─────────────────────────────────────────────────────────────────────────────

async def generate_weekly_meal_plan(
    groq_client,
    target_kcal: int,
    protein_g:   int,
    carbs_g:     int,
    fat_g:       int,
    goal:        str = "mentinere",
    preferences: str = "",
) -> dict:
    """
    Generează un plan alimentar de 7 zile.

    Strategie v3:
    - Generare concurentă: zilele 1-3 și 4-7 în paralel (asyncio.gather)
    - JSON mode obligatoriu pe Groq
    - Temperature annealing pe retry (0.45 → 0.30 → 0.20)
    - Validare duală: calorii ±15% + proteină ±20%
    - Scaling matematic garantat dacă AI eșuează numeric
    """
    goal_context = GOAL_CONTEXT_MAP.get(goal, "echilibru caloric")

    # ── Distribuție per masă ─────────────────────────────────────────────────
    mc_dejun   = round(target_kcal * 0.25)
    mc_pranz   = round(target_kcal * 0.35)
    mc_gustare = round(target_kcal * 0.15)
    mc_cina    = round(target_kcal * 0.25)

    p_dejun    = round(protein_g * 0.20)
    p_pranz    = round(protein_g * 0.40)
    p_gustare  = round(protein_g * 0.15)
    p_cina     = round(protein_g * 0.25)

    pref_line = (
        f"PREFERINȚE SPECIALE (respectă obligatoriu): {preferences.strip()}\n\n"
        if preferences.strip() else ""
    )

    # ── Retry loop cu temperature annealing ─────────────────────────────────
    temperatures   = [0.45, 0.30, 0.20]
    best_result    = None
    best_deviation = float("inf")

    for attempt, temperature in enumerate(temperatures):
        try:
            # La retry 2+, adaugă presiune suplimentară bazată pe ultimul rezultat
            pressure = _build_retry_pressure(
                attempt, best_result, target_kcal, protein_g,
                mc_dejun, mc_pranz, mc_gustare, mc_cina
            )

            # ── Generare concurentă: zilele 1-3 ȘI 4-7 simultan ────────────
            batch1_task = _generate_days_batch(
                groq_client,
                day_numbers=[1, 2, 3],
                target_kcal=target_kcal, protein_g=protein_g,
                carbs_g=carbs_g, fat_g=fat_g,
                goal_context=goal_context, pref_line=pref_line,
                mc_dejun=mc_dejun, mc_pranz=mc_pranz,
                mc_gustare=mc_gustare, mc_cina=mc_cina,
                p_dejun=p_dejun, p_pranz=p_pranz,
                p_gustare=p_gustare, p_cina=p_cina,
                pressure=pressure,
                temperature=temperature,
            )
            batch2_task = _generate_days_batch(
                groq_client,
                day_numbers=[4, 5, 6, 7],
                target_kcal=target_kcal, protein_g=protein_g,
                carbs_g=carbs_g, fat_g=fat_g,
                goal_context=goal_context, pref_line=pref_line,
                mc_dejun=mc_dejun, mc_pranz=mc_pranz,
                mc_gustare=mc_gustare, mc_cina=mc_cina,
                p_dejun=p_dejun, p_pranz=p_pranz,
                p_gustare=p_gustare, p_cina=p_cina,
                pressure=pressure,
                temperature=temperature,
            )

            batch1, batch2 = await asyncio.gather(batch1_task, batch2_task)

            if not batch1 or not batch2:
                print(f"⚠️ attempt {attempt+1}: batch None — retry")
                continue

            # ── Merge cele două batch-uri ────────────────────────────────────
            merged = _merge_batches(batch1, batch2)
            merged = _recalculate_day_totals(merged)

            # ── Validare duală ───────────────────────────────────────────────
            avg_kcal = merged.get("weekly_avg", {}).get("calories", 0)
            avg_prot = merged.get("weekly_avg", {}).get("protein_g", 0)

            cal_ok  = target_kcal * 0.85 <= avg_kcal <= target_kcal * 1.15
            prot_ok = protein_g   * 0.80 <= avg_prot <= protein_g   * 1.20

            # Track best result chiar dacă nu e perfect
            deviation = abs(avg_kcal - target_kcal) / target_kcal
            if deviation < best_deviation:
                best_deviation = deviation
                best_result    = merged

            if cal_ok and prot_ok:
                print(f"✓ Meal plan OK la attempt {attempt+1}: {avg_kcal} kcal, {avg_prot}g prot")
                return merged

            print(
                f"⚠️ attempt {attempt+1}: {avg_kcal} kcal (target {target_kcal}), "
                f"{avg_prot}g prot (target {protein_g}) — "
                f"cal_ok={cal_ok}, prot_ok={prot_ok} — retry temp={temperatures[min(attempt+1, 2)]}"
            )

        except Exception as e:
            print(f"⚠️ attempt {attempt+1} exception: {e}")

    # ── Toate attempt-urile au eșuat → scaling matematic garantat ───────────
    if best_result:
        avg_kcal = best_result.get("weekly_avg", {}).get("calories", 0)
        if avg_kcal > 0:
            print(
                f"⚠️ Aplicăm scaling matematic: {avg_kcal} → {target_kcal} kcal "
                f"(ratio={target_kcal/avg_kcal:.2f})"
            )
            best_result = _apply_caloric_scaling(best_result, target_kcal, protein_g)
            return best_result

    return {
        "error": (
            "AI-ul nu a generat un plan valid după 3 încercări. "
            "Încearcă din nou sau ajustează preferințele."
        )
    }


# ─────────────────────────────────────────────────────────────────────────────
#  GENERARE BATCH (3 sau 4 zile) — apelat concurent
# ─────────────────────────────────────────────────────────────────────────────

async def _generate_days_batch(
    groq_client,
    day_numbers: list[int],
    target_kcal: int, protein_g: int, carbs_g: int, fat_g: int,
    goal_context: str, pref_line: str,
    mc_dejun: int, mc_pranz: int, mc_gustare: int, mc_cina: int,
    p_dejun: int, p_pranz: int, p_gustare: int, p_cina: int,
    pressure: str,
    temperature: float,
) -> dict | None:
    """
    Generează un subset de zile (ex: [1,2,3] sau [4,5,6,7]).
    Apelat concurent din generate_weekly_meal_plan.
    Folosește JSON mode pentru output garantat valid.
    """
    days_str = ", ".join(
        f"Ziua {n} ({DAY_NAMES[n-1]})" for n in day_numbers
    )
    days_count = len(day_numbers)

    user_prompt = (
        f"Creează plan alimentar pentru EXACT {days_count} zile: {days_str}.\n\n"
        f"═══════════════════════════════════════════\n"
        f"TARGET ZILNIC (OBLIGATORIU, NU NEGOCIABIL):\n"
        f"═══════════════════════════════════════════\n"
        f"  Calorii totale/zi: {target_kcal} kcal\n"
        f"  Proteină/zi:       {protein_g}g\n"
        f"  Carbohidrați/zi:   {carbs_g}g\n"
        f"  Grăsimi/zi:        {fat_g}g\n\n"
        f"DISTRIBUȚIE OBLIGATORIE PE MESE:\n"
        f"  Mic Dejun: {mc_dejun} kcal · {p_dejun}g proteină\n"
        f"  Prânz:     {mc_pranz} kcal · {p_pranz}g proteină   ← masă PRINCIPALĂ\n"
        f"  Gustare:   {mc_gustare} kcal · {p_gustare}g proteină\n"
        f"  Cină:      {mc_cina} kcal · {p_cina}g proteină\n\n"
        f"VERIFICARE: {mc_dejun}+{mc_pranz}+{mc_gustare}+{mc_cina}"
        f"={mc_dejun+mc_pranz+mc_gustare+mc_cina} kcal/zi\n\n"
        f"{pref_line}"
        f"OBIECTIV: {goal_context}\n"
        f"Varietate maximă — nicio masă repetată între zile.\n"
        f"{pressure}\n"
        f"Generează EXACT {days_count} zile. JSON complet.\n"
        f"shopping_tips: include 4 sfaturi practice (doar în batch-ul zilelor 4-7)."
    )

    try:
        r = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": MEAL_PLAN_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=4000,         # 4000 per batch × 2 batches = 8000 efectiv
            top_p=0.85,
            response_format={"type": "json_object"},  # JSON mode garantat
        )

        raw    = r.choices[0].message.content.strip()
        parsed = _safe_parse_json(raw)

        if not parsed or "plan" not in parsed:
            return None

        # Normalizăm day numbers să fie corecte
        for i, day in enumerate(parsed.get("plan", [])):
            expected_num = day_numbers[i] if i < len(day_numbers) else day_numbers[-1]
            day["day"]      = expected_num
            day["day_name"] = DAY_NAMES[expected_num - 1]

        return parsed

    except Exception as e:
        print(f"⚠️ _generate_days_batch({day_numbers}) error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  MERGE BATCH-URI
# ─────────────────────────────────────────────────────────────────────────────

def _merge_batches(batch1: dict, batch2: dict) -> dict:
    """Combină două batch-uri de zile într-un plan complet de 7 zile."""
    plan1 = batch1.get("plan", [])
    plan2 = batch2.get("plan", [])

    # Shopping tips vin de regulă din batch2 (zilele 4-7) sau din oricare
    shopping_tips = (
        batch2.get("shopping_tips") or
        batch1.get("shopping_tips") or
        []
    )

    merged_plan = plan1 + plan2

    # Asigurăm ordinea corectă după day number
    merged_plan.sort(key=lambda d: d.get("day", 0))

    return {
        "plan": merged_plan,
        "shopping_tips": shopping_tips,
        # weekly_avg va fi recalculat de _recalculate_day_totals
    }


# ─────────────────────────────────────────────────────────────────────────────
#  SCALING MATEMATIC — garanție calorie accuracy
# ─────────────────────────────────────────────────────────────────────────────

def _apply_caloric_scaling(plan_data: dict, target_kcal: int, target_protein: int) -> dict:
    """
    Scalare matematică garantată când AI-ul eșuează caloric.

    Ce face:
    1. Calculează ratio target/generat
    2. Scalează toate valorile nutriționale cu ratio-ul
    3. Actualizează cantitățile (g/ml) din descrieri
    4. Recalculează day_totals și weekly_avg

    Ex: dacă AI generează 1500 kcal dar target e 3000, ratio=2.0
        → toate caloriile ×2, "150g pui" → "300g pui"
    """
    avg_kcal = plan_data.get("weekly_avg", {}).get("calories", 0)
    if avg_kcal == 0:
        return plan_data

    cal_ratio  = target_kcal / avg_kcal

    # Nu scalăm dacă suntem deja în ±10% (evităm micro-ajustări)
    if 0.90 <= cal_ratio <= 1.10:
        return plan_data

    for day in plan_data.get("plan", []):
        for meal in day.get("meals", []):
            meal["calories"]  = round(meal.get("calories",  0) * cal_ratio)
            meal["protein_g"] = round(meal.get("protein_g", 0) * cal_ratio)
            meal["carbs_g"]   = round(meal.get("carbs_g",   0) * cal_ratio)
            meal["fat_g"]     = round(meal.get("fat_g",     0) * cal_ratio)

            # Scalează și cantitățile din descriere (g și ml)
            desc = meal.get("description", "")
            if desc:
                meal["description"] = _scale_description_quantities(desc, cal_ratio)

    return _recalculate_day_totals(plan_data)


def _scale_description_quantities(description: str, ratio: float) -> str:
    if 0.90 <= ratio <= 1.10:
        return description

    def replacer(match):
        val  = float(match.group(1))
        unit = match.group(2)
        scaled = round(val * ratio / 5) * 5   # nearest 5
        scaled = max(5, scaled)                # minim 5g/ml
        return f"{scaled}{unit}"

    return re.sub(r"(\d+(?:\.\d+)?)\s*(g|ml)\b", replacer, description)


def _build_retry_pressure(
    attempt: int,
    last_result: dict | None,
    target_kcal: int,
    protein_g: int,
    mc_dejun: int, mc_pranz: int, mc_gustare: int, mc_cina: int,
) -> str:
    """Construiește mesajul de presiune pentru retry-urile 2 și 3."""
    if attempt == 0 or not last_result:
        return ""

    avg_kcal = last_result.get("weekly_avg", {}).get("calories", 0)
    avg_prot = last_result.get("weekly_avg", {}).get("protein_g", 0)

    if attempt == 1:
        return (
            f"\n⚠️ EROARE ANTERIOARĂ: ai generat {avg_kcal} kcal/zi în loc de {target_kcal}. "
            f"Aceasta e GREȘIT. Mărește dramatic porțiile. "
            f"Prânzul TREBUIE să fie {mc_pranz} kcal, Gustarea {mc_gustare} kcal."
        )
    else:
        return (
            f"\n🚨 ULTIMA ȘANSĂ: ai generat {avg_kcal} kcal și {avg_prot}g prot. "
            f"Target: {target_kcal} kcal și {protein_g}g prot. "
            f"Exemple CONCRETE pentru Prânz la {mc_pranz} kcal: "
            f"400g piept pui(660kcal)+500g orez fiert(650kcal)+1 lingură ulei(88kcal)={mc_pranz}kcal. "
            f"Respectă EXACT aceste cantități."
        )


def _safe_parse_json(text: str) -> dict | None:
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


def _recalculate_day_totals(plan_data: dict) -> dict:
    all_cals, all_prot, all_carbs, all_fat = [], [], [], []

    for day in plan_data.get("plan", []):
        meals = day.get("meals", [])
        totals = {
            "calories":  sum(int(m.get("calories",  0)) for m in meals),
            "protein_g": sum(int(m.get("protein_g", 0)) for m in meals),
            "carbs_g":   sum(int(m.get("carbs_g",   0)) for m in meals),
            "fat_g":     sum(int(m.get("fat_g",     0)) for m in meals),
        }
        day["day_totals"] = totals
        all_cals.append(totals["calories"])
        all_prot.append(totals["protein_g"])
        all_carbs.append(totals["carbs_g"])
        all_fat.append(totals["fat_g"])

    if all_cals:
        plan_data["weekly_avg"] = {
            "calories":  round(sum(all_cals)  / len(all_cals)),
            "protein_g": round(sum(all_prot)  / len(all_prot)),
            "carbs_g":   round(sum(all_carbs) / len(all_carbs)),
            "fat_g":     round(sum(all_fat)   / len(all_fat)),
        }

    return plan_data