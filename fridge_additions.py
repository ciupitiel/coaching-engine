# =============================================================================
#  fridge_additions.py — "Ce am în frigider?" Feature
#  Noian Lab · Powered by Groq Vision + Llama 3.3 70B
#  -----------------------------------------------------------------------------
#  POST /food/fridge
#    Input:  imagine frigider (base64) SAU text ingrediente
#    Output: plan alimentar 1 zi (4 mese) folosind strict ingredientele găsite
#
#  Flux:
#    1. [dacă imagine] Groq Vision identifică ingredientele vizibile
#    2. [dacă text] parsare directă
#    3. Groq 70B generează ziua: mic dejun / prânz / gustare / cină
#       → folosind NUMAI ingredientele disponibile + baze (ulei, sare, usturoi)
#    4. Returnează plan JSON complet cu macros + prep time
#
#  Feature gratuit (nu premium) — driver de achiziție și viralitate.
#
#  Adaugă în main.py:
#    from fridge_additions import init_fridge_router
#    app.include_router(init_fridge_router(groq_client))
# =============================================================================

import json
import re
import logging
from typing import Optional

from fastapi   import APIRouter, Depends, HTTPException
from pydantic  import BaseModel, Field

from auth              import require_user_email
from database          import get_pool
from database_settings import get_settings

logger = logging.getLogger(__name__)

VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
VISION_FALLBACK = "llama-3.2-11b-vision-preview"
TEXT_MODEL   = "llama-3.3-70b-versatile"


# ─────────────────────────────────────────────────────────────────────────────
#  MODELE
# ─────────────────────────────────────────────────────────────────────────────

class FridgeRequest(BaseModel):
    image_base64: Optional[str] = Field(
        default=None,
        description="Data URL JPEG/PNG a frigiderului (opțional)"
    )
    ingredients_text: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Listă ingrediente text (opțional): 'ouă, pui, broccoli'"
    )
    target_kcal: Optional[int] = Field(default=None, ge=1000, le=6000)
    goal: Optional[str] = Field(default="mentinere")


# ─────────────────────────────────────────────────────────────────────────────
#  PROMPTS
# ─────────────────────────────────────────────────────────────────────────────

_VISION_INGREDIENT_PROMPT = """Ești un expert care identifică alimente și ingrediente din imagini.
Uită-te cu atenție la imaginea frigiderului/cămării și listează TOT ce văd.

Returnează EXCLUSIV JSON valid:
{
  "ingredients": ["ingredient1", "ingredient2", "ingredient3"],
  "notes": "observații dacă e cazul sau string gol"
}

Reguli:
- Listează fiecare ingredient vizibil (nu marca, ci tipul: "lapte", "ouă", "piept pui")
- Include cantități aproximative dacă sunt vizibile: "ouă (6 buc)", "lapte (1L)"
- Ingrediente comune prezente mereu (condimente, ulei etc.) NUMAI dacă le vezi
- Dacă nu e o imagine cu mâncare/frigider: {"ingredients": [], "notes": "Nu am identificat alimente"}
- JSON valid, fără text suplimentar"""


def _build_meal_plan_prompt(
    ingredients: list[str],
    target_kcal: int,
    goal: str,
) -> str:
    ing_list = "\n".join(f"  - {i}" for i in ingredients)

    goal_map = {
        "cut_bland":  "slăbire ușoară, protein ridicat, legume multe",
        "cut":        "slăbire moderată, porții mai mici, protein very high",
        "mentinere":  "menținere, macros echilibrate",
        "bulk_lean":  "masă curată, carbohidrați la prânz, protein ridicat",
        "bulk":       "surplus caloric, porții mari, mese dense",
    }
    goal_desc = goal_map.get(goal, "echilibru caloric")

    # Distribuție per masă
    mc_dej  = round(target_kcal * 0.25)
    mc_pran = round(target_kcal * 0.35)
    mc_gust = round(target_kcal * 0.15)
    mc_cin  = round(target_kcal * 0.25)

    return f"""Ești un nutriționist și chef specializat în bucătăria românească.
Generează un plan alimentar pentru O ZI folosind EXCLUSIV ingredientele disponibile.

INGREDIENTE DISPONIBILE:
{ing_list}

BAZE DISPONIBILE MEREU (nu trebuie să le menționezi): ulei, sare, piper, usturoi, apă

TARGET CALORIC: {target_kcal} kcal/zi
OBIECTIV: {goal_desc}

DISTRIBUȚIE PE MESE:
  Mic Dejun: ~{mc_dej} kcal
  Prânz:     ~{mc_pran} kcal  ← cea mai consistentă masă
  Gustare:   ~{mc_gust} kcal
  Cină:      ~{mc_cin} kcal

REGULI ABSOLUTE:
- Folosești NUMAI ingredientele din lista de mai sus + bazele
- Nu inventa ingrediente care nu sunt în listă
- Dacă lipsesc ingrediente pentru o masă completă, adaptezi cu ce există
- Cantități exacte în grame/ml în description
- Rețete simple, fezabile, gustoase

Returnează EXCLUSIV JSON valid:
{{
  "plan": {{
    "mic_dejun": {{
      "name": "Denumire masă",
      "description": "Xg ingredient1, Yg ingredient2, ...",
      "calories": {mc_dej},
      "protein_g": 0,
      "carbs_g": 0,
      "fat_g": 0,
      "prep_time": "X min"
    }},
    "pranz": {{...}},
    "gustare": {{...}},
    "cina": {{...}}
  }},
  "total_calories": {target_kcal},
  "total_protein_g": 0,
  "total_carbs_g": 0,
  "total_fat_g": 0,
  "notes": "sfaturi scurte despre pregătire sau substituții"
}}"""


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITARE
# ─────────────────────────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict | None:
    for attempt in [text, re.sub(r"```(?:json)?", "", text).replace("```", "").strip()]:
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


def _validate_plan(data: dict, target_kcal: int) -> dict:
    """Sanitizează și recalculează totalele."""
    plan = data.get("plan", {})
    meals = ["mic_dejun", "pranz", "gustare", "cina"]
    meal_labels = {
        "mic_dejun": "Mic Dejun",
        "pranz":     "Prânz",
        "gustare":   "Gustare",
        "cina":      "Cină",
    }

    total_cal = total_prot = total_carb = total_fat = 0
    fixed_plan = {}

    for meal_key in meals:
        m = plan.get(meal_key, {})
        cal  = max(0, int(float(m.get("calories",  0) or 0)))
        prot = max(0, int(float(m.get("protein_g", 0) or 0)))
        carb = max(0, int(float(m.get("carbs_g",   0) or 0)))
        fat  = max(0, int(float(m.get("fat_g",     0) or 0)))

        fixed_plan[meal_key] = {
            "label":       meal_labels[meal_key],
            "name":        str(m.get("name", f"Masă {meal_key}")),
            "description": str(m.get("description", "—")),
            "calories":    cal,
            "protein_g":   prot,
            "carbs_g":     carb,
            "fat_g":       fat,
            "prep_time":   str(m.get("prep_time", "—")),
        }
        total_cal  += cal
        total_prot += prot
        total_carb += carb
        total_fat  += fat

    return {
        "plan":            fixed_plan,
        "total_calories":  total_cal,
        "total_protein_g": total_prot,
        "total_carbs_g":   total_carb,
        "total_fat_g":     total_fat,
        "notes":           str(data.get("notes", "")),
    }


def _parse_text_ingredients(text: str) -> list[str]:
    """Parsează lista de ingrediente din text liber."""
    separators = r"[,;\n\r]+"
    parts = re.split(separators, text)
    ingredients = []
    for p in parts:
        p = p.strip(" \t-•·*").strip()
        if p and len(p) >= 2:
            ingredients.append(p)
    return ingredients[:30]   # max 30 ingrediente


async def _identify_ingredients_from_image(
    groq_client, image_data: str
) -> list[str]:
    """Folosește Vision pentru a identifica ingredientele din imagine."""
    b64 = image_data.split(",", 1)[1] if "," in image_data else image_data
    mime = "image/png" if "image/png" in image_data else "image/jpeg"
    data_url = f"data:{mime};base64,{b64}"

    for model in [VISION_MODEL, VISION_FALLBACK]:
        try:
            r = await groq_client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text",      "text": _VISION_INGREDIENT_PROMPT},
                    ],
                }],
                temperature=0.10,
                max_tokens=600,
            )
            raw    = r.choices[0].message.content or ""
            parsed = _parse_json(raw)
            if parsed and parsed.get("ingredients"):
                logger.info("Fridge vision: %d ingrediente detectate cu %s",
                            len(parsed["ingredients"]), model)
                return parsed["ingredients"]
        except Exception as e:
            logger.warning("Fridge vision error (%s): %s", model, e)

    return []


async def _generate_day_plan(
    groq_client,
    ingredients: list[str],
    target_kcal: int,
    goal: str,
) -> dict | None:
    """Generează planul zilei cu Llama 3.3 70B."""
    prompt = _build_meal_plan_prompt(ingredients, target_kcal, goal)

    try:
        r = await groq_client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.35,
            max_tokens=2000,
        )
        raw    = r.choices[0].message.content or ""
        parsed = _parse_json(raw)
        if parsed and "plan" in parsed:
            return _validate_plan(parsed, target_kcal)
    except Exception as e:
        logger.error("Fridge plan generation error: %s", e)

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  ROUTER FACTORY
# ─────────────────────────────────────────────────────────────────────────────

def init_fridge_router(groq_client) -> APIRouter:
    router = APIRouter(prefix="/food", tags=["Fridge Planner"])

    @router.post("/fridge")
    async def fridge_planner(
        req:   FridgeRequest,
        email: str = Depends(require_user_email),
    ):
        """
        "Ce am în frigider?" — generează planul zilei din ingredientele disponibile.

        Acceptă:
          - image_base64: fotografie frigider/cămară → Vision detectează ingredientele
          - ingredients_text: "ouă, pui, broccoli, orez" → parsare directă
          - Sau ambele combinate (Vision + text adăugat manual)

        Returnează plan 4 mese cu macros, prep time și note.
        Feature gratuit — driver de achiziție.
        """
        if not req.image_base64 and not req.ingredients_text:
            raise HTTPException(
                400,
                "Trimite fie o imagine, fie o listă de ingrediente (sau ambele)."
            )

        # ── Obține targetul caloric din profil dacă nu e specificat ──────────
        target_kcal = req.target_kcal
        goal        = req.goal or "mentinere"

        if not target_kcal:
            try:
                async with get_pool().acquire() as conn:
                    row = await conn.fetchrow(
                        """
                        SELECT target_kcal, goal FROM sessions
                        WHERE LOWER(user_email) = LOWER($1)
                        ORDER BY timestamp DESC LIMIT 1
                        """,
                        email,
                    )
                if row:
                    target_kcal = int(row["target_kcal"])
                    goal        = row["goal"] or goal
            except Exception:
                pass

        target_kcal = target_kcal or 2000   # fallback rezonabil

        # ── Identifică ingredientele ──────────────────────────────────────────
        ingredients: list[str] = []

        # Din imagine (dacă există)
        if req.image_base64 and len(req.image_base64) > 10_000:
            vision_ings = await _identify_ingredients_from_image(
                groq_client, req.image_base64
            )
            ingredients.extend(vision_ings)

        # Din text (adăugat sau fallback)
        if req.ingredients_text:
            text_ings = _parse_text_ingredients(req.ingredients_text)
            # Adaugă doar ce nu există deja (deduplicare simplă)
            existing_lower = {i.lower() for i in ingredients}
            for ing in text_ings:
                if ing.lower() not in existing_lower:
                    ingredients.append(ing)
                    existing_lower.add(ing.lower())

        if not ingredients:
            raise HTTPException(
                422,
                "Nu am putut identifica niciun ingredient. "
                "Încearcă o fotografie mai clară sau listează ingredientele manual."
            )

        # ── Generează planul zilei ────────────────────────────────────────────
        plan = await _generate_day_plan(groq_client, ingredients, target_kcal, goal)

        if not plan:
            raise HTTPException(
                503,
                "Generarea planului a eșuat. Încearcă din nou."
            )

        return {
            "ok":                  True,
            "ingredients_found":   ingredients,
            "ingredients_count":   len(ingredients),
            "target_kcal":         target_kcal,
            **plan,
        }

    return router