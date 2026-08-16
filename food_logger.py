import json
import re

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTE
# ─────────────────────────────────────────────────────────────────────────────

MEAL_TYPES: dict[str, str] = {
    "mic_dejun": "Mic Dejun",
    "pranz":     "Prânz",
    "cina":      "Cină",
    "gustare":   "Gustare",
    "general":   "General",
}

# ─────────────────────────────────────────────────────────────────────────────
#  SYSTEM PROMPT — Specialist nutriție românească
#  Regula #1: RETURNEZI EXCLUSIV JSON VALID. Zero text extra.
#  Regula #2: temperature=0.15 ajută, dar prompt-ul e linia de apărare principală.
# ─────────────────────────────────────────────────────────────────────────────

FOOD_PARSE_SYSTEM_PROMPT = """Ești un nutriționist specializat în alimentația românească.
Primești o descriere de mâncare și returnezi EXCLUSIV JSON VALID.
Zero text suplimentar. Zero explicații. Zero markdown. Doar JSON parsabil.

STRUCTURĂ JSON EXACTĂ — respectă cheile exact:
{
  "items": [
    {
      "name": "Denumire produs în română",
      "quantity": "1 bucată (~300g)",
      "calories": 520,
      "protein_g": 28,
      "carbs_g": 48,
      "fat_g": 22
    }
  ],
  "totals": {
    "calories": 520,
    "protein_g": 28,
    "carbs_g": 48,
    "fat_g": 22
  },
  "confidence": "high",
  "notes": "Estimare bazată pe porție standard românească"
}

REGULI CRITICE:
· Porții STANDARD ROMÂNEȘTI (nu americane): șaormă=300g, ciorbă=400ml, mici=2buc, pizza felie=120g
· Dacă cantitatea nu e specificată → porție standard românească
· confidence: "high"=produs clar definit / "medium"=estimare rezonabilă / "low"=descriere vagă
· totals = suma EXACTĂ din items (calcul manual, nu estima)
· Valorile nutriționale sunt per PORȚIE SERVITĂ (nu per 100g)
· Apă, cafea neagră, ceai neîndulcit → 0 kcal, menționat în notes, NU în items
· Dacă descrierea e complet de neînțeles → returnează items=[], totals zeros, confidence="low"

REFERINȚE NUTRIȚIONALE ROMÂNEȘTI (memorează aceste valori de bază):
Șaormă pui 300g:         520 kcal  P:28  C:48  G:22
Șaormă vită 300g:        560 kcal  P:30  C:48  G:25
Mici 2 buc 120g:         320 kcal  P:22  C:2   G:26
Ciorbă burtă 400ml:      280 kcal  P:18  C:8   G:18
Ciorbă legume 400ml:     120 kcal  P:4   C:20  G:3
Mămăligă 200g:           180 kcal  P:5   C:38  G:1
Sarmale 3 buc 300g:      420 kcal  P:22  C:30  G:24
Piept pui grătar 150g:   210 kcal  P:40  C:0   G:5
Piept pui fiert 150g:    195 kcal  P:38  C:0   G:4
Orez fiert 200g:         260 kcal  P:5   C:56  G:1
Cartofi prăjiți 150g:    320 kcal  P:4   C:40  G:17
Cartofi fierți 200g:     160 kcal  P:4   C:36  G:0
Pâine felie 30g:         75 kcal   P:3   C:14  G:1
Ou fiert 1 buc:          78 kcal   P:6   C:0   G:5
Ouă omletă 2 buc:        200 kcal  P:14  C:1   G:16
Iaurt natural 125g:      70 kcal   P:4   C:5   G:4
Brânză telemea 50g:      130 kcal  P:8   C:1   G:11
Lapte 200ml:             100 kcal  P:7   C:10  G:4
Cola/suc carbogazos 330ml: 140 kcal P:0  C:35  G:0
Pizza felie 120g:        280 kcal  P:12  C:36  G:10
Cozonac felie 80g:       290 kcal  P:6   C:48  G:10
Biscuiți 50g:            240 kcal  P:3   C:34  G:11
Ciocolată 25g:           135 kcal  P:2   C:15  G:8
Banană 1 buc 120g:       105 kcal  P:1   C:27  G:0
Măr 1 buc 150g:          78 kcal   P:0   C:21  G:0
Portocală 1 buc 130g:    60 kcal   P:1   C:15  G:0"""


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIE PRINCIPALĂ
# ─────────────────────────────────────────────────────────────────────────────

async def parse_food_description(groq_client, description: str) -> dict:
    """
    Parsează o descriere naturală de mâncare și returnează macronutrienții estimați.

    Suportă română și limbi mixte — AI-ul înțelege contextul.
    Exemple valide: "o șaormă cu cartofi", "100g piept pui + orez", "sandwich cu brânză".

    Args:
        groq_client : instanța AsyncGroq din main.py (reutilizată — nu recreăm clientul)
        description : text liber de la utilizator, max 500 caractere

    Returns:
        Success: {items, totals, confidence, notes, raw_description}
        Eroare:  {error, raw_description}
    """
    try:
        r = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": FOOD_PARSE_SYSTEM_PROMPT},
                {"role": "user",   "content": f"Estimează nutrițional: {description}"},
            ],
            temperature=0.15,   # Low temperature = JSON consistent, nu răspunsuri creative
            max_tokens=700,
            top_p=0.9,
        )

        raw    = r.choices[0].message.content.strip()
        parsed = _safe_parse_json(raw)

        if not parsed:
            return {
                "error":           "AI-ul nu a returnat un JSON valid. Încearcă o descriere mai clară.",
                "raw_description": description,
            }

        parsed["raw_description"] = description
        return _validate_and_fix(parsed)

    except Exception as e:
        return {
            "error":           f"Eroare la procesare: {str(e)}",
            "raw_description": description,
        }


def build_food_log_summary(logs: list[dict], target_macros: dict | None) -> dict:
    """
    Agregă logurile dintr-o zi și calculează progresul față de targetul macro.

    Args:
        logs         : food_logs din DB pentru ziua curentă [{calories, protein_g, ...}]
        target_macros: din ultimul calcul TDEE {target_kcal, protein_g, carbs_g, fat_g}

    Returns:
        {
            daily_totals: {calories, protein_g, carbs_g, fat_g},
            logs_count: int,
            progress?:  {calories_pct, protein_pct, carbs_pct, fat_pct, remaining_kcal},
            targets?:   {calories, protein_g, carbs_g, fat_g}
        }
    """
    daily = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
    for log in logs:
        for key in daily:
            daily[key] += int(log.get(key, 0))

    result: dict = {"daily_totals": daily, "logs_count": len(logs)}

    if target_macros:
        t_kcal = int(target_macros.get("target_kcal") or target_macros.get("total_kcal") or 2000)
        t_prot = int(target_macros.get("protein_g") or 0)
        t_carb = int(target_macros.get("carbs_g")   or 0)
        t_fat  = int(target_macros.get("fat_g")     or 0)

        result["progress"] = {
            "calories_pct":   min(100, round(daily["calories"]  / t_kcal * 100)) if t_kcal else 0,
            "protein_pct":    min(100, round(daily["protein_g"] / t_prot * 100)) if t_prot else 0,
            "carbs_pct":      min(100, round(daily["carbs_g"]   / t_carb * 100)) if t_carb else 0,
            "fat_pct":        min(100, round(daily["fat_g"]     / t_fat  * 100)) if t_fat  else 0,
            "remaining_kcal": max(0, t_kcal - daily["calories"]),
        }
        result["targets"] = {
            "calories":  t_kcal,
            "protein_g": t_prot,
            "carbs_g":   t_carb,
            "fat_g":     t_fat,
        }

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITARE INTERNE
# ─────────────────────────────────────────────────────────────────────────────

def _safe_parse_json(text: str) -> dict | None:
    """
    Parsează JSON din răspunsul AI, robust la markdown fences și text extra.
    Trei strategii în ordine: direct, fără markdown, extragere bloc JSON.
    """
    for attempt in [text, re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()]:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            pass

    # Extrage primul bloc { ... } din text
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


def _validate_and_fix(parsed: dict) -> dict:
    """
    Normalizează structura JSON de la AI și recalculează totalurile corect.
    Nu avem încredere că AI-ul a adunat corect — recalculăm noi înșine.
    """
    parsed.setdefault("items", [])

    # Normalizăm fiecare item — valorile numerice trebuie să fie int
    for item in parsed["items"]:
        for key in ("calories", "protein_g", "carbs_g", "fat_g"):
            try:
                item[key] = int(round(float(item.get(key, 0))))
            except (TypeError, ValueError):
                item[key] = 0

    # Recalculăm totalurile din items (sursa de adevăr)
    if parsed["items"]:
        parsed["totals"] = {
            k: sum(item.get(k, 0) for item in parsed["items"])
            for k in ("calories", "protein_g", "carbs_g", "fat_g")
        }
    else:
        parsed.setdefault("totals", {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0})
        for key in ("calories", "protein_g", "carbs_g", "fat_g"):
            try:
                parsed["totals"][key] = int(round(float(parsed["totals"].get(key, 0))))
            except (TypeError, ValueError):
                parsed["totals"][key] = 0

    parsed.setdefault("confidence", "medium")
    parsed.setdefault("notes", "")

    return parsed