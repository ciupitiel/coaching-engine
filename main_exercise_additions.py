# =============================================================================
#  main_exercise_additions.py — #16: Exerciții & Calorii Arse · Router
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Adaugă în main.py EXACT 4 linii:
#
#  ① La importuri (după `from main_barcode_additions import init_barcode_router`):
#       from database_exercise import init_db_exercise
#       from main_exercise_additions import init_exercise_router
#
#  ② În lifespan(), DUPĂ `await init_db_push()` (linia 144 din main.py):
#       await init_db_exercise()
#
#  ③ La routers (după `app.include_router(init_barcode_router())`):
#       app.include_router(init_exercise_router(groq_client))
#
#  Total modificări în main.py: 4 linii noi. Zero înlocuiri.
#  -----------------------------------------------------------------------------
#  Endpoint-uri expuse:
#    POST /exercise/log            → NL → AI parse → kcal → DB
#    GET  /exercise/today          → loguri azi + sumar (kcal arse, minute)
#    DELETE /exercise/log/{id}     → ștergere log specific
#    GET  /exercise/types          → categorii disponibile (dropdown UI)
#    GET  /exercise/history?days=7 → agregat ultimele N zile
#
#  Integrare în dashboard:
#    · /exercise/today se integrează lângă /food/today pentru overview zilnic
#    · calories_burned din sumar poate fi afișat în header: "Azi: +1800 -320 = 1480 net"
#    · /exercise/history alimentează un grafic Chart.js separat (opțional)
#
#  Rate limit recomandat (adaugă în rate_limiter.py → DAILY_LIMITS):
#    "/exercise/log": 50,   ← Groq call per log
# =============================================================================

import datetime as _dt
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from auth import require_user_email
from database import get_profile
from database_exercise import (
    save_exercise_log,
    get_exercise_logs_by_date,
    delete_exercise_log,
    get_exercise_logs_range,
    get_exercise_summary_today,
)
from exercise_logger import parse_exercise_description, EXERCISE_TYPES, INTENSITY_LABELS


# ─────────────────────────────────────────────────────────────────────────────
#  MODELE PYDANTIC
# ─────────────────────────────────────────────────────────────────────────────

class ExerciseLogRequest(BaseModel):
    description:   str = Field(..., min_length=3, max_length=500,
                                description="Descriere liberă a antrenamentului, în română.")
    exercise_type: str = Field(
        default="",
        description="Opțional: cardio | forta | flexibilitate | sport | general. "
                    "Dacă lipsește, AI detectează automat."
    )


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY ROUTER — același pattern ca P4, P5, P6, Barcode
# ─────────────────────────────────────────────────────────────────────────────

def init_exercise_router(groq_client) -> APIRouter:
    """
    Creează și returnează router-ul #16 cu groq_client capturat în closure.
    Apelat O SINGURĂ DATĂ la pornire din main.py:
        app.include_router(init_exercise_router(groq_client))
    """
    router = APIRouter(prefix="/exercise", tags=["#16 · Exerciții & Calorii Arse"])

    # ── POST /exercise/log ────────────────────────────────────────────────────
    @router.post("/log")
    async def exercise_log_post(
        req:   ExerciseLogRequest,
        email: str = Depends(require_user_email),
    ):
        """
        Pipeline complet: descriere liberă → AI parse → kcal calculate → DB.

        Flux:
          1. Extrage greutatea din profil (necesară pentru formula MET × kg)
          2. Groq/Llama parsează → exercise_name, met_value, duration_min
          3. Backend calculează: calories_burned = MET × weight_kg × (min/60)
          4. Salvează în exercise_logs
          5. Returnează parsed complet pentru update imediat în UI

        Fallback greutate: 75 kg (medie statistică adultă) dacă nu există profil.
        Userului i se arată weight_used_kg → poate vedea pe ce bază s-a calculat.

        Exemple de input valid:
          "am alergat 30 de minute"
          "1 oră la sală, greutăți și piept"
          "yoga 45 min"
          "am mers 8000 de pași azi"
          "10km ciclism, ritm lejer"
        """
        # ── Greutate curentă din profil ───────────────────────────────────────
        # Folsim initial_weight_kg ca proxy — e cea mai recentă greutate introdusă
        # manual în calculator. Viitor: putem prelua ultima valoare din weight_checkins.
        profile   = await get_profile(email)
        weight_kg = float((profile or {}).get("initial_weight_kg") or 75.0)

        # ── Parse AI ─────────────────────────────────────────────────────────
        parsed = await parse_exercise_description(groq_client, req.description, weight_kg)

        if "error" in parsed:
            raise HTTPException(status_code=422, detail=parsed["error"])

        # ── Override exercise_type (dacă userul l-a specificat explicit) ──────
        # UI-ul poate trimite exercise_type dintr-un dropdown — are prioritate față de AI.
        exercise_type = req.exercise_type.strip()
        if exercise_type not in EXERCISE_TYPES:
            exercise_type = parsed["exercise_type"]  # fallback la AI detection

        # ── Salvare DB ────────────────────────────────────────────────────────
        saved = await save_exercise_log(
            email=email,
            description=req.description,
            exercise_name=parsed["exercise_name"],
            exercise_type=exercise_type,
            duration_min=parsed["duration_min"],
            calories_burned=parsed["calories_burned"],
            met_value=parsed["met_value"],
            intensity=parsed["intensity"],
            notes=parsed.get("notes", ""),
        )

        return {
            "ok":     True,
            "log_id": saved["id"],
            "date":   saved["date"],
            "parsed": parsed,   # UI-ul afișează breakdown imediat
        }

    # ── GET /exercise/today ───────────────────────────────────────────────────
    @router.get("/today")
    async def exercise_today_get(email: str = Depends(require_user_email)):
        """
        Returnează logurile de azi + sumar calorii arse și minute active.

        Structura răspunsului:
          logs    : [{id, exercise_name, exercise_type, duration_min, calories_burned, ...}]
          summary : {total_logs, calories_burned, total_minutes, date}
          date    : "YYYY-MM-DD"

        Poate fi combinat cu /food/today pentru overview net caloric zilnic:
          net_kcal = food_today.summary.daily_totals.calories - exercise_today.summary.calories_burned
        """
        today   = _dt.datetime.now().strftime("%Y-%m-%d")
        logs    = await get_exercise_logs_by_date(email, today)
        summary = await get_exercise_summary_today(email)

        return {
            "logs":    logs,
            "summary": summary,
            "date":    today,
        }

    # ── DELETE /exercise/log/{log_id} ─────────────────────────────────────────
    @router.delete("/log/{log_id}")
    async def exercise_log_delete(
        log_id: int,
        email:  str = Depends(require_user_email),
    ):
        """
        Șterge un log specific.
        Verificarea emailului din DB → nu poți șterge logurile altor useri.
        """
        deleted = await delete_exercise_log(email, log_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail="Log negăsit sau nu îți aparține.",
            )
        return {"ok": True, "deleted_id": log_id}

    # ── GET /exercise/types ───────────────────────────────────────────────────
    @router.get("/types")
    async def exercise_types_get():
        """
        Returnează tipurile de exerciții + intensitățile disponibile.
        Folosit de dropdown-uri dinamice în UI (fără hardcoding în frontend).
        """
        return {
            "exercise_types": [
                {"value": k, "label": v}
                for k, v in EXERCISE_TYPES.items()
            ],
            "intensity_levels": [
                {"value": k, "label": v}
                for k, v in INTENSITY_LABELS.items()
            ],
        }

    # ── GET /exercise/history ─────────────────────────────────────────────────
    @router.get("/history")
    async def exercise_history_get(
        days:  int = 7,
        email: str = Depends(require_user_email),
    ):
        """
        Istoricul antrenamentelor din ultimele N zile, grupat pe date.

        Query param:
          days : numărul de zile (default 7, acceptat 1-90)

        Structura răspunsului:
          logs        : lista plată a tuturor logurilor (pentru tabel)
          by_date     : {
                          "YYYY-MM-DD": {
                              logs: [...],
                              calories_burned: int,
                              total_minutes: int
                          }
                        }
          total_burned : total kcal arse în perioadă
          avg_daily    : medie kcal/zi (pe zilele ACTIVE, nu pe toate)
          days_active  : zile cu cel puțin un antrenament
          period_days  : intervalul analizat (N)

        Exemplu pentru grafic săptămânal Chart.js:
          x: Object.keys(by_date)
          y: Object.values(by_date).map(d => d.calories_burned)
        """
        if not (1 <= days <= 90):
            days = 7

        today      = _dt.date.today()
        start_date = str(today - _dt.timedelta(days=days - 1))
        end_date   = str(today)

        logs = await get_exercise_logs_range(email, start_date, end_date)

        # ── Grupare pe date ──────────────────────────────────────────────────
        by_date: dict[str, dict] = {}
        for log in logs:
            d = log.get("date", "")
            if not d:
                continue
            if d not in by_date:
                by_date[d] = {"logs": [], "calories_burned": 0, "total_minutes": 0}
            by_date[d]["logs"].append(log)
            by_date[d]["calories_burned"] += int(log.get("calories_burned", 0))
            by_date[d]["total_minutes"]   += int(log.get("duration_min", 0))

        total_burned = sum(v["calories_burned"] for v in by_date.values())
        days_active  = len(by_date)
        avg_daily    = round(total_burned / days_active) if days_active > 0 else 0

        return {
            "logs":         logs,
            "by_date":      by_date,
            "total_burned": total_burned,
            "avg_daily":    avg_daily,
            "days_active":  days_active,
            "period_days":  days,
            "start_date":   start_date,
            "end_date":     end_date,
        }

    return router