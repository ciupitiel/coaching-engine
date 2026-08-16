# =============================================================================
#  main_p5_additions.py — P5: Meal Plan Generator Router
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Modul NOU. Adaugă în main.py exact 2 linii noi:
#
#  ① La importuri (lângă `from main_p4_additions import init_food_router`):
#     from main_p5_additions import init_meal_plan_router
#
#  ② Imediat după `app.include_router(init_food_router(groq_client))`:
#     app.include_router(init_meal_plan_router(groq_client))
#
#  Total modificări în main.py: 2 linii noi. Zero înlocuiri.
# =============================================================================

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from premium_guard import require_premium
from meal_plan_generator import generate_weekly_meal_plan
from database import get_user_sessions, get_profile
from auth import require_user_email


# ─────────────────────────────────────────────────────────────────────────────
#  MODEL PYDANTIC — P5
# ─────────────────────────────────────────────────────────────────────────────

class MealPlanRequest(BaseModel):
    preferences: str = ""   # ex: "fără pești", "vegetarian", "lactate puține"


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY FUNCȚIE — același pattern ca P4 (closure pentru groq_client)
# ─────────────────────────────────────────────────────────────────────────────

def init_meal_plan_router(groq_client) -> APIRouter:
    """
    Creează și returnează router-ul P5 cu groq_client capturat în closure.

    Pattern: factory function — elimină riscul de import circular și
    nu necesită variabile globale.

    Apelat O SINGURĂ DATĂ la pornire, în main.py:
        app.include_router(init_meal_plan_router(groq_client))
    """
    router = APIRouter(prefix="/meal-plan", tags=["P5 · Meal Plan Generator"])

    # ── POST /meal-plan/generate ──────────────────────────────────────────────
    @router.post("/generate")
    async def endpoint_generate_meal_plan(
        req:   MealPlanRequest,
        email: str = Depends(require_premium),
    ):
        """
        Generează un plan alimentar de 7 zile personalizat.

        Flux:
          1. Trage macros din ultimul calcul TDEE al userului (sessions table)
          2. Trage obiectivul din profilul persistent (user_profiles table)
          3. Trimite la Groq/Llama cu targetul exact
          4. Recalculează day_totals local
          5. Returnează planul complet

        Notă: generarea durează 10-25 secunde (model 70B + 4000 tokens output).
        Frontend-ul trebuie să afișeze un loading state corespunzător.
        """
        # ── Verificare date necesare ────────────────────────────────────────
        sessions     = await get_user_sessions(email, limit=1)
        last_session = sessions[0] if sessions else None

        if not last_session:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Efectuează mai întâi un calcul TDEE din pagina principală. "
                    "AI-ul are nevoie de macros-urile tale pentru a genera planul."
                )
            )

        # ── Extrage datele necesare ──────────────────────────────────────────
        profile = await get_profile(email)
        goal    = (profile or {}).get("goal", "mentinere")

        target_kcal = int(last_session.get("target_kcal") or 2000)
        protein_g   = int(last_session.get("protein_g")   or 150)
        carbs_g     = int(last_session.get("carbs_g")     or 200)
        fat_g       = int(last_session.get("fat_g")       or 70)

        # ── Generare AI ─────────────────────────────────────────────────────
        plan = await generate_weekly_meal_plan(
            groq_client=groq_client,
            target_kcal=target_kcal,
            protein_g=protein_g,
            carbs_g=carbs_g,
            fat_g=fat_g,
            goal=goal,
            preferences=req.preferences,
        )

        if "error" in plan:
            raise HTTPException(status_code=500, detail=plan["error"])

        # ── Atașăm metadate pentru frontend ─────────────────────────────────
        plan["meta"] = {
            "target_kcal": target_kcal,
            "protein_g":   protein_g,
            "carbs_g":     carbs_g,
            "fat_g":       fat_g,
            "goal":        goal,
            "days_count":  len(plan.get("plan", [])),
        }

        return plan

    # ── GET /meal-plan/targets ────────────────────────────────────────────────
    @router.get("/targets")
    async def endpoint_get_targets(email: str = Depends(require_premium)):
        """
        Returnează macro targets din ultimul calcul TDEE.
        Folosit de frontend pentru a afișa ce target va fi folosit
        ÎNAINTE ca userul să apese Generate.
        """
        sessions     = await get_user_sessions(email, limit=1)
        last_session = sessions[0] if sessions else None

        if not last_session:
            return {"has_targets": False}

        profile = await get_profile(email)
        goal    = (profile or {}).get("goal", "mentinere")

        return {
            "has_targets": True,
            "target_kcal": last_session.get("target_kcal"),
            "protein_g":   last_session.get("protein_g"),
            "carbs_g":     last_session.get("carbs_g"),
            "fat_g":       last_session.get("fat_g"),
            "goal":        goal,
        }

    return router