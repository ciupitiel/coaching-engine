# =============================================================================
#  main_coach_additions.py — #14: Coach Dashboard · Admin API
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Adaugă în main.py EXACT 4 linii:
#
#  ① La importuri (lângă celelalte main_pX_additions):
#       from main_coach_additions import init_coach_router
#
#  ② La router registration (după celelalte include_router):
#       app.include_router(init_coach_router())
#
#  ③ O rută nouă pentru pagina HTML (lângă /landing, /terms, /privacy):
#       @app.get("/coach", include_in_schema=False)
#       async def serve_coach():
#           return FileResponse("coach.html")
#
#  ④ În .env (și în Render Dashboard → Environment Variables):
#       ADMIN_EMAIL=emailul_tau@domeniu.ro
#
#  Protecție:
#    · JWT valid (Bearer token) — aceeași verificare ca la celelalte rute
#    · Email din token TREBUIE să fie ADMIN_EMAIL din .env
#    · Orice alt user primește 403 Forbidden
#
#  Endpoint-uri expuse:
#    GET /coach/summary           → statistici globale (rapid, fără loop)
#    GET /coach/clients           → toți clienții cu stats agregate
#    GET /coach/client/{email}    → detalii complete pentru un client
#
#  Notă performanță:
#    _client_card_data() face ~7 query-uri per user.
#    Pentru <100 clienți (scara actuală) e acceptabil.
#    Viitor: un singur query multi-join optimizat când apare nevoia.
# =============================================================================

import os
import datetime

from fastapi import APIRouter, Depends, HTTPException
from auth import require_user_email
from database import get_pool, get_checkins, get_user_sessions
from database_streak import compute_streak
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN CHECK — dependency FastAPI
# ─────────────────────────────────────────────────────────────────────────────

async def require_admin(email: str = Depends(require_user_email)) -> str:
    """
    Verifică că userul autentificat este administratorul aplicației.

    Admin = emailul din variabila de mediu ADMIN_EMAIL.
    Dacă ADMIN_EMAIL nu e setat → 503 (configurare lipsă).
    Dacă emailul nu coincide → 403 (access denied).

    De ce email și nu ADMIN_SECRET (ca la /push/trigger-daily)?
    → Dashboardul e o pagină vizitată de browser cu JWT, nu un curl admin.
      Verificăm identitatea prin token-ul deja existent, fără un header extra.
    """
    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    if not admin_email:
        raise HTTPException(
            status_code=503,
            detail=(
                "ADMIN_EMAIL nesetat în .env. "
                "Adaugă: ADMIN_EMAIL=emailul_tau@domeniu.ro"
            ),
        )
    if email.lower() != admin_email:
        raise HTTPException(
            status_code=403,
            detail="Acces restricționat. Dashboard disponibil doar pentru admin.",
        )
    return email


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITARE DB
# ─────────────────────────────────────────────────────────────────────────────

async def _all_users() -> list[dict]:
    """
    Returnează toți userii înregistrați cu date de bază.
    Sortat: cel mai nou primul (pentru a vedea clienți noi sus).
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT email, created_at,
                   COALESCE(is_verified, TRUE) AS is_verified
            FROM users
            ORDER BY created_at DESC
            """
        )
    return [dict(r) for r in rows]


async def _client_card_data(email: str) -> dict:
    """
    Agregate stats pentru un client — folosit în lista /coach/clients.

    7 query-uri separate (asyncpg nu permite $1 multiplu în același query):
      1. Ultimul check-in de greutate
      2. Total check-in-uri
      3. Ultimele 4 check-in-uri (pentru detecție stagnare)
      4. Ultima sesiune TDEE
      5. Data ultimului food log
      6. Total food logs
      7. Media zilnică calorii ultimele 7 zile

    Stagnare: ultimele 3 check-in-uri în ±0.4 kg pe minim 7 zile
              ȘI obiectivul nu e menținere.
    Inactiv:  >3 zile fără niciun food log.
    """
    pool = get_pool()
    async with pool.acquire() as conn:

        # 1. Ultimul check-in greutate
        wrow = await conn.fetchrow(
            """
            SELECT weight_kg, date
            FROM weight_checkins
            WHERE LOWER(user_email) = LOWER($1)
            ORDER BY date DESC LIMIT 1
            """,
            email,
        )

        # 2. Total check-in-uri
        checkin_count = await conn.fetchval(
            "SELECT COUNT(*) FROM weight_checkins WHERE LOWER(user_email) = LOWER($1)",
            email,
        )

        # 3. Ultimele 4 check-in-uri (pentru stagnare)
        recent_ci = await conn.fetch(
            """
            SELECT weight_kg, date FROM weight_checkins
            WHERE LOWER(user_email) = LOWER($1)
            ORDER BY date DESC LIMIT 4
            """,
            email,
        )

        # 4. Ultima sesiune TDEE
        srow = await conn.fetchrow(
            """
            SELECT target_kcal, protein_g, carbs_g, fat_g, goal, tdee
            FROM sessions
            WHERE LOWER(user_email) = LOWER($1)
            ORDER BY timestamp DESC LIMIT 1
            """,
            email,
        )

        # 5. Data ultimului food log
        last_food_date = await conn.fetchval(
            "SELECT MAX(date) FROM food_logs WHERE LOWER(user_email) = LOWER($1)",
            email,
        )

        # 6. Total food logs
        total_food = await conn.fetchval(
            "SELECT COUNT(*) FROM food_logs WHERE LOWER(user_email) = LOWER($1)",
            email,
        )

        # 7. Media zilnică calorii — ultimele 7 zile (zile cu loguri)
        seven_ago = str(datetime.date.today() - datetime.timedelta(days=7))
        avg_row = await conn.fetchrow(
            """
            SELECT
                AVG(day_cal)::integer  AS avg_kcal,
                COUNT(*)               AS logged_days
            FROM (
                SELECT date, SUM(calories) AS day_cal
                FROM food_logs
                WHERE LOWER(user_email) = LOWER($1)
                  AND date >= $2
                GROUP BY date
            ) t
            """,
            email, seven_ago,
        )

    # ── Stagnare ──────────────────────────────────────────────────────────────
    goal = srow["goal"] if srow else "mentinere"
    stagnation = False

    if len(recent_ci) >= 3 and goal != "mentinere":
        weights = [float(r["weight_kg"]) for r in recent_ci[:3]]
        variance = round(max(weights) - min(weights), 2)

        # Calculăm intervalul de zile acoperit de ultimele 3 check-in-uri
        dates = [r["date"] for r in recent_ci[:3]]
        oldest = datetime.date.fromisoformat(str(min(dates)))
        newest = datetime.date.fromisoformat(str(max(dates)))
        span_days = (newest - oldest).days

        stagnation = (variance <= 0.4 and span_days >= 7)
    else:
        variance  = None
        span_days = None

    # ── Activitate food logging ────────────────────────────────────────────────
    days_since_log = None
    if last_food_date:
        days_since_log = (
            datetime.date.today()
            - datetime.date.fromisoformat(str(last_food_date))
        ).days

    # ── Compliance caloric 7 zile ─────────────────────────────────────────────
    compliance_pct = None
    avg_kcal_7d    = None
    logged_days_7d = 0

    if avg_row and avg_row["avg_kcal"] and srow and srow["target_kcal"]:
        avg_kcal_7d    = int(avg_row["avg_kcal"])
        logged_days_7d = int(avg_row["logged_days"] or 0)
        compliance_pct = min(150, round(avg_kcal_7d / int(srow["target_kcal"]) * 100))

    # ── Alert principal (prioritate: stagnare > inactiv) ─────────────────────
    alert = None
    if stagnation:
        alert = "stagnare"
    elif days_since_log is not None and days_since_log > 3:
        alert = "inactiv"

    return {
        "email":          email,
        "last_weight":    float(wrow["weight_kg"]) if wrow else None,
        "last_checkin":   str(wrow["date"])         if wrow else None,
        "checkin_count":  int(checkin_count or 0),
        "goal":           goal,
        "target_kcal":    srow["target_kcal"]        if srow else None,
        "protein_g":      srow["protein_g"]           if srow else None,
        "carbs_g":        srow["carbs_g"]             if srow else None,
        "fat_g":          srow["fat_g"]               if srow else None,
        "tdee":           srow["tdee"]                if srow else None,
        "total_food_logs": int(total_food or 0),
        "last_food_date": str(last_food_date)         if last_food_date else None,
        "days_since_log": days_since_log,
        "avg_kcal_7d":    avg_kcal_7d,
        "logged_days_7d": logged_days_7d,
        "compliance_pct": compliance_pct,
        "stagnation":     stagnation,
        "stagnation_variance": variance,
        "stagnation_span":     span_days,
        "alert":          alert,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY ROUTER
# ─────────────────────────────────────────────────────────────────────────────
class CoachRecommendRequest(BaseModel):
    message: str = Field(..., min_length=5, max_length=1000)
def init_coach_router() -> APIRouter:
    """
    Creează și returnează router-ul Coach Dashboard.
    Apelat O SINGURĂ DATĂ la pornirea serverului din main.py:
        app.include_router(init_coach_router())
    """
    router = APIRouter(prefix="/coach", tags=["Coach Dashboard"])

    # ── GET /coach/summary ────────────────────────────────────────────────────
    @router.get("/summary")
    async def coach_summary(admin: str = Depends(require_admin)):
        """
        Statistici globale rapide: total clienți, verificați, activi azi, nou această săptămână.
        Un singur query group → răspuns instant chiar și cu sute de useri.
        """
        today     = str(datetime.date.today())
        week_ago  = str(datetime.date.today() - datetime.timedelta(days=7))

        async with get_pool().acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM users")

            verified = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE COALESCE(is_verified, TRUE) = TRUE"
            )

            logged_today = await conn.fetchval(
                """
                SELECT COUNT(DISTINCT LOWER(user_email))
                FROM food_logs WHERE date = $1
                """,
                today,
            )

            new_this_week = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE created_at >= $1",
                week_ago,
            )

            stagnation_count = await conn.fetchval(
                """
                SELECT COUNT(DISTINCT LOWER(user_email))
                FROM food_logs
                WHERE date < $1
                  AND date >= $2
                """,
                str(datetime.date.today() - datetime.timedelta(days=3)),
                str(datetime.date.today() - datetime.timedelta(days=30)),
            )

        return {
            "total":         int(total or 0),
            "verified":      int(verified or 0),
            "logged_today":  int(logged_today or 0),
            "new_this_week": int(new_this_week or 0),
            "generated_at":  datetime.datetime.now().isoformat(),
        }

    # ── GET /coach/clients ────────────────────────────────────────────────────
    @router.get("/clients")
    async def coach_clients(admin: str = Depends(require_admin)):
        """
        Returnează lista completă a clienților cu statisticile de bază.

        Sortare:
          1. Clienții cu alerte (stagnare → inactiv) înaintea celor fără
          2. Streak descrescător în interiorul fiecărui grup

        Performanță: O(N × 8 query-uri) — acceptabil pentru <100 clienți.
        """
        users   = await _all_users()
        clients = []

        for u in users:
            card = await _client_card_data(u["email"])

            # Streak — din database_streak (calculat din food_logs)
            try:
                streak_data    = await compute_streak(u["email"])
                current_streak = streak_data.get("current_streak", 0)
                longest_streak = streak_data.get("longest_streak", 0)
                logged_today   = streak_data.get("logged_today",   False)
            except Exception:
                current_streak = 0
                longest_streak = 0
                logged_today   = False

            clients.append({
                **card,
                "is_verified":    bool(u.get("is_verified", True)),
                "created_at":     u.get("created_at"),
                "current_streak": current_streak,
                "longest_streak": longest_streak,
                "logged_today":   logged_today,
            })

        # Sort: alerte → streak descrescător
        clients.sort(key=lambda c: (
            {"stagnare": 0, "inactiv": 1}.get(c.get("alert") or "", 2),
            -(c.get("current_streak") or 0),
        ))

        alerts_count = sum(1 for c in clients if c.get("alert"))

        return {
            "clients":      clients,
            "total":        len(clients),
            "alerts_count": alerts_count,
            "generated_at": datetime.datetime.now().isoformat(),
        }

    # ── GET /coach/client/{email} ─────────────────────────────────────────────
    @router.get("/client/{email}")
    async def coach_client_detail(email: str, admin: str = Depends(require_admin)):
        """
        Detalii complete pentru un client specific.

        Include:
          · card (statisticile de bază, ca în /clients)
          · checkins (ultimele 30 de greutăți, ASC pentru grafic)
          · sessions (ultimele 3 calcule TDEE)
          · streak (complet, cu last_7_days pentru dots)
          · food_14d (statistici food logging ultimele 14 zile)
        """
        from food_adaptive_bridge import get_food_intake_stats

        card     = await _client_card_data(email)
        checkins = await get_checkins(email, limit=30)   # ASC — perfect pentru grafic
        sessions = await get_user_sessions(email, limit=3)
        streak   = await compute_streak(email)
        food_14d = await get_food_intake_stats(email, days=14)

        return {
            "card":     card,
            "checkins": checkins,
            "sessions": sessions,
            "streak":   streak,
            "food_14d": food_14d,
        }

# ── GET /coach/admin-stats ────────────────────────────────────────────────
    @router.get("/admin-stats")
    async def admin_stats(admin: str = Depends(require_admin)):
        """
        Statistici extinse pentru Admin Dashboard.
        Combinate cu /coach/summary pentru imaginea completă.
        """
        today    = datetime.date.today()
        wks12ago = str(today - datetime.timedelta(weeks=12))

        async with get_pool().acquire() as conn:
            # ── Premium ───────────────────────────────────────────────────
            premium_active  = await conn.fetchval(
                "SELECT COUNT(*) FROM user_stripe WHERE is_premium = TRUE"
            ) or 0
            cancelled_total = await conn.fetchval(
                "SELECT COUNT(*) FROM user_stripe WHERE subscription_status='cancelled'"
            ) or 0

            # ── Feature usage ─────────────────────────────────────────────
            food_logs_n    = await conn.fetchval("SELECT COUNT(*) FROM food_logs")     or 0
            exercise_logs_n= await conn.fetchval("SELECT COUNT(*) FROM exercise_logs") or 0
            checkins_n     = await conn.fetchval("SELECT COUNT(*) FROM weight_checkins") or 0
            sessions_n     = await conn.fetchval("SELECT COUNT(*) FROM user_sessions") or 0

            # ── Active useri (logged ceva în ultimele 7 zile) ─────────────
            active_7d = await conn.fetchval(
                """
                SELECT COUNT(DISTINCT LOWER(user_email))
                FROM food_logs WHERE date >= $1
                """,
                str(today - datetime.timedelta(days=7)),
            ) or 0

            # ── Growth: signup-uri pe zi, ultimele 12 săptămâni ──────────
            growth_rows = await conn.fetch(
                "SELECT LEFT(created_at,10) AS day FROM users WHERE LEFT(created_at,10) >= $1 ORDER BY day",
                wks12ago,
            )

            # ── Top 10 alimente ───────────────────────────────────────────
            top_foods = await conn.fetch(
                """
                SELECT description, COUNT(*) AS cnt
                FROM food_logs
                GROUP BY description
                ORDER BY cnt DESC
                LIMIT 10
                """
            )

            # ── Activitate recentă Premium ────────────────────────────────
            recent_prem = await conn.fetch(
                """
                SELECT user_email, subscription_status, created_at
                FROM user_stripe
                ORDER BY created_at DESC
                LIMIT 12
                """
            )

        # ── Agregare growth pe săptămâni (Python, nu SQL — date ca TEXT) ──
        from collections import defaultdict
        week_counts: dict = defaultdict(int)
        for row in growth_rows:
            d      = datetime.date.fromisoformat(row["day"])
            monday = d - datetime.timedelta(days=d.weekday())
            week_counts[str(monday)] += 1
        growth = [{"week": k, "users": v} for k, v in sorted(week_counts.items())]

        def _mask(email: str) -> str:
            p = email.split("@")
            if len(p) != 2: return "***"
            n = p[0]
            return (n[:2] if len(n) > 2 else n[0]) + "***@" + p[1]

        return {
            "premium": {
                "active":    int(premium_active),
                "cancelled": int(cancelled_total),
            },
            "revenue": {
                "mrr":      round(float(premium_active) * 7.0, 2),
                "arr":      round(float(premium_active) * 7.0 * 12, 2),
                "currency": "EUR",
            },
            "activity": {"active_7d": int(active_7d)},
            "feature_usage": {
                "food_logs":     int(food_logs_n),
                "exercise_logs": int(exercise_logs_n),
                "checkins":      int(checkins_n),
                "tdee_calcs":    int(sessions_n),
            },
            "growth":         growth,
            "top_foods":      [
                {"name": r["description"][:48], "count": int(r["cnt"])}
                for r in top_foods
            ],
            "recent_premium": [
                {
                    "email":  _mask(r["user_email"]),
                    "status": r["subscription_status"],
                    "date":   str(r["created_at"])[:10],
                }
                for r in recent_prem
            ],
            "generated_at": datetime.datetime.now().isoformat(),
        }

# ── POST /coach/recommend/{client_email} ──────────────────────────────────
    @router.post("/recommend/{client_email}")
    async def send_coach_recommendation(
        client_email: str,
        req: CoachRecommendRequest,
        admin: str = Depends(require_admin),
    ):
        """Trimite o recomandare unui client specific."""
        from database_coach_v2 import send_recommendation
        if not req.message or len(req.message.strip()) < 5:
            raise HTTPException(status_code=400, detail="Mesajul e prea scurt.")
        rec_id = await send_recommendation(
            coach_email=admin,
            client_email=client_email,
            message=req.message.strip(),
        )
        return {"ok": True, "id": rec_id}

    # ── GET /coach/recommend/{client_email} ───────────────────────────────────
    @router.get("/recommend/{client_email}")
    async def get_coach_recommendations(
        client_email: str,
        admin: str = Depends(require_admin),
    ):
        """Returnează recomandările trimise de coach unui client."""
        from database_coach_v2 import get_recommendations_sent_by_coach
        recs = await get_recommendations_sent_by_coach(admin, client_email)
        return {"recommendations": recs}

    # ── GET /coach/my-recommendations (pentru client în index.html) ───────────
    @router.get("/my-recommendations")
    async def my_recommendations(email: str = Depends(require_user_email)):
        """Client vede recomandările primite de la coach. Marchează automat ca citite."""
        from database_coach_v2 import get_recommendations_for_client, mark_recommendations_read
        recs = await get_recommendations_for_client(email)
        await mark_recommendations_read(email)
        return {"recommendations": recs}

    # ── GET /coach/unread-count (badge pentru client) ─────────────────────────
    @router.get("/unread-count")
    async def unread_count(email: str = Depends(require_user_email)):
        """Returnează numărul de recomandări necitite."""
        from database_coach_v2 import count_unread_recommendations
        n = await count_unread_recommendations(email)
        return {"unread": n}

    return router