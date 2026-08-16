# =============================================================================
#  main_morning_additions.py — Morning Plan Confirm · Router
#  Noian Cristian · Noian Lab
#  -----------------------------------------------------------------------------
#  Adaugă în main.py exact 3 linii:
#
#  ① La importuri (după `from main_push_additions import init_push_router`):
#       from main_morning_additions import init_morning_router
#       from database_morning_plan import init_db_morning_plan
#
#  ② În lifespan(), după `await init_db_templates()`:
#       await init_db_morning_plan()
#
#  ③ La routers, după `app.include_router(init_push_router())`:
#       app.include_router(init_morning_router())
#
#  Endpoint-uri expuse:
#    POST /morning/confirm  → validează token UUID, bulk insert în food_logs,
#                             marchează planul ca logat (single-use)
#    GET  /morning/today    → returnează planul AI de azi (JWT obligatoriu)
#    POST /morning/trigger  → admin: declanșează manual job-ul de dimineață
#
#  Securitate /morning/confirm:
#    · Token UUID v4 single-use din tabelul morning_plans — nu necesită JWT
#    · Service worker-ul nu are acces la localStorage (unde e JWT-ul)
#    · already_confirmed → idempotent: SW poate retry fără duplicate în food_logs
#    · confidence='high': planul AI e bazat pe macros reale ale userului —
#      cel mai precis tip de log posibil, mai bun decât food_logger (medium)
# =============================================================================

import os
import json
import datetime

from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel, Field

from auth import require_user_email
from database import get_pool
from database_morning_plan import get_plan_by_token, mark_plan_confirmed
from database_p4_additions import save_food_log
from push_engine import send_morning_plans


# ─────────────────────────────────────────────────────────────────────────────
#  MODELE PYDANTIC
# ─────────────────────────────────────────────────────────────────────────────

class MorningConfirmRequest(BaseModel):
    token: str = Field(
        ...,
        min_length=36,
        max_length=36,
        description="UUID v4 din payload-ul push · single-use",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  HELPER INTERN
# ─────────────────────────────────────────────────────────────────────────────

# meal_type → etichetă afișabilă în notes (UI food log)
_MEAL_LABELS: dict[str, str] = {
    "mic_dejun": "Mic Dejun",
    "pranz":     "Prânz",
    "cina":      "Cină",
}


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY ROUTER
# ─────────────────────────────────────────────────────────────────────────────

def init_morning_router() -> APIRouter:
    """
    Router Morning Plan.
    Apelat O SINGURĂ DATĂ în main.py: app.include_router(init_morning_router())
    Pattern identic cu init_push_router(), init_streak_router() etc.
    """
    router = APIRouter(prefix="/morning", tags=["Morning Plan"])

    # ── POST /morning/confirm ─────────────────────────────────────────────────
    @router.post("/confirm")
    async def morning_confirm(req: MorningConfirmRequest):
        """
        Confirmă planul de dimineață și îl loghează bulk în food_logs.

        Apelat din:
          · Service worker — action 'confirm-log' din notificationclick
          · Frontend — butonul "Loghez tot" din panoul Morning Plan

        NU necesită JWT — token UUID v4 single-use este autentificarea.
        Service worker-ul nu are acces la localStorage (unde stă Bearer token-ul).

        Flux:
          1. get_plan_by_token(token) → email, plan_date, meals, already_confirmed
          2. already_confirmed=True → returnează ok=True, idempotent=True (zero duplicate)
          3. Bulk INSERT (3 mese) în food_logs · confidence='high'
          4. mark_plan_confirmed(token) → confirmed_at = now · token inactiv

        items_json: structură identică cu food_logger.py pentru compatibilitate
        completă cu get_food_logs_by_date(), build_food_log_summary() și UI-ul
        existent din tab Nutriție.
        """
        plan = await get_plan_by_token(req.token)

        if not plan:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Token invalid. Planul de dimineață nu a fost generat sau "
                    "token-ul a expirat la re-generare."
                ),
            )

        # Idempotent — SW-ul poate retry (network failure, offline → retry) fără duplicate
        if plan["already_confirmed"]:
            return {
                "ok":           True,
                "idempotent":   True,
                "logs_created": 0,
                "total_kcal":   0,
                "message":      "Planul era deja logat. Niciun log nou creat.",
            }

        email     = plan["user_email"]
        plan_date = plan["plan_date"]   # YYYY-MM-DD (informativ în response)
        meals     = plan["meals"]       # list[dict] din plan_json

        logs_created = 0
        total_kcal   = 0

        for meal in meals:
            meal_type   = meal.get("meal_type", "general")
            description = meal.get("name", "Masă plan AI")
            calories    = int(meal.get("calories", 0))
            protein_g   = int(meal.get("protein_g", 0))
            carbs_g     = int(meal.get("carbs_g", 0))
            fat_g       = int(meal.get("fat_g", 0))

            # items_json: identic cu food_logger pentru UI-ul existent
            items = [{
                "name":      description,
                "calories":  calories,
                "protein_g": protein_g,
                "carbs_g":   carbs_g,
                "fat_g":     fat_g,
                "quantity":  1,
                "unit":      "portie",
            }]

            await save_food_log(
                email=email,
                meal_type=meal_type,
                description=description,
                calories=calories,
                protein_g=protein_g,
                carbs_g=carbs_g,
                fat_g=fat_g,
                items=items,
                confidence="high",   # Plan AI pe macros reale > recunoaștere foto (medium)
                notes=f"Plan AI · {_MEAL_LABELS.get(meal_type, meal_type)}",
            )

            logs_created += 1
            total_kcal   += calories

        # Marchează token-ul ca single-use — apeluri ulterioare → idempotent branch
        await mark_plan_confirmed(req.token)

        print(
            f"✅  Morning confirm: {email} · {logs_created} mese · "
            f"{total_kcal} kcal · plan_date={plan_date}"
        )

        return {
            "ok":           True,
            "idempotent":   False,
            "logs_created": logs_created,
            "total_kcal":   total_kcal,
            "plan_date":    plan_date,
            "message":      f"{logs_created} mese logate · {total_kcal} kcal total.",
        }

    # ── GET /morning/today ────────────────────────────────────────────────────
    @router.get("/today")
    async def morning_today(email: str = Depends(require_user_email)):
        """
        Returnează planul AI de azi al userului autentificat.
        Frontend-ul îl afișează în tab Nutriție (secțiunea "Planul tău de azi").

        Response dacă există plan:
            {
                "available":    true,
                "plan_date":    "2025-08-08",
                "meals":        [{meal_label, meal_type, name, description,
                                  calories, protein_g, carbs_g, fat_g}],
                "confirmed":    false,
                "confirmed_at": null,
                "token":        "uuid-v4",
                "created_at":   "2025-08-08T08:00:01"
            }

        Response dacă nu există (user fără TDEE sau job n-a rulat încă):
            { "available": false, "plan_date": "2025-08-08" }
        """
        today = datetime.date.today().isoformat()

        async with get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT plan_json, token, confirmed_at, created_at
                FROM morning_plans
                WHERE LOWER(user_email) = LOWER($1) AND plan_date = $2
                """,
                email, today,
            )

        if not row:
            return {"available": False, "plan_date": today}

        return {
            "available":    True,
            "plan_date":    today,
            "meals":        json.loads(row["plan_json"]),
            "confirmed":    row["confirmed_at"] is not None,
            "confirmed_at": row["confirmed_at"],
            "token":        row["token"],
            "created_at":   row["created_at"],
        }

    # ── POST /morning/trigger ─────────────────────────────────────────────────
    @router.post("/trigger", include_in_schema=False)
    async def morning_trigger(
        x_admin_secret: str | None = Header(default=None, alias="X-Admin-Secret"),
    ):
        """
        Declanșează manual job-ul de dimineață (admin only).
        Pattern identic cu /push/trigger-daily.

        Curl:
            curl -X POST https://api.noianlab.ro/morning/trigger \\
                 -H "X-Admin-Secret: secretul_din_env"
        """
        admin_secret = os.getenv("ADMIN_SECRET", "")
        if not admin_secret or x_admin_secret != admin_secret:
            raise HTTPException(status_code=403, detail="Acces interzis.")

        await send_morning_plans()
        return {"ok": True, "message": "Job dimineață declanșat manual."}

    return router