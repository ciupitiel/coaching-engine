# =============================================================================
#  nutritionist_additions.py — Nutritionist B2B Platform · API Routes
#  Noian Lab
#  -----------------------------------------------------------------------------
#  v2 — Optimizări față de v1:
#  - Batch queries: 4 queries totale pentru N clienți (în loc de N×5)
#  - asyncio la nivel de modul, nu în interiorul funcțiilor
#  - Semaphore pe detalii client (max 5 concurrent)
#  - Erori granulare cu logging
# =============================================================================

import asyncio
import datetime
import logging
import os
from itertools import groupby

from fastapi           import APIRouter, Depends, HTTPException
from pydantic          import BaseModel, EmailStr, Field

from auth                  import require_user_email
from database              import get_pool
from database_nutritionist import (
    create_nutritionist, get_nutritionist, get_nutritionist_by_invite_code,
    is_nutritionist, update_nutritionist_profile,
    link_client_to_nutritionist, get_nutritionist_clients,
    remove_client_from_nutritionist,
)
from database_coach_v2 import (
    send_recommendation,
    get_recommendations_sent_by_coach,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  MODELE
# ─────────────────────────────────────────────────────────────────────────────

class NutritionistRegister(BaseModel):
    name:          str = Field(..., min_length=2, max_length=100)
    business_name: str = Field(default="", max_length=120)


class NutritionistUpdate(BaseModel):
    name:          str | None = None
    business_name: str | None = None


class AddClientRequest(BaseModel):
    client_email: EmailStr


class RecommendRequest(BaseModel):
    message: str = Field(..., min_length=5, max_length=2000)


# ─────────────────────────────────────────────────────────────────────────────
#  AUTH GUARD
# ─────────────────────────────────────────────────────────────────────────────

async def require_nutritionist(email: str = Depends(require_user_email)) -> str:
    if not await is_nutritionist(email):
        raise HTTPException(403, "Acces restricționat — cont de nutriționist necesar.")
    return email


# ─────────────────────────────────────────────────────────────────────────────
#  BATCH QUERY — toți clienții dintr-o singură trecere prin DB
#  4 queries indiferent de N clienți (în loc de N×5 queries în paralel)
# ─────────────────────────────────────────────────────────────────────────────

async def _get_clients_summary_batch(client_emails: list[str]) -> list[dict]:
    """
    Returnează statistici de bază pentru toți clienții nutriționistului.
    Execută EXACT 4 queries, indiferent de câți clienți sunt.
    """
    if not client_emails:
        return []

    today    = datetime.date.today().isoformat()
    week_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    emails_lower = [e.lower() for e in client_emails]

    async with get_pool().acquire() as conn:
        # ── 1. Ultima sesiune TDEE per client ─────────────────────────────
        sessions_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (LOWER(user_email))
                LOWER(user_email) AS email,
                target_kcal, protein_g, goal, weight_kg, tdee
            FROM sessions
            WHERE LOWER(user_email) = ANY($1::text[])
            ORDER BY LOWER(user_email), timestamp DESC
            """,
            emails_lower,
        )

        # ── 2. Ultimul check-in greutate per client ────────────────────────
        checkin_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (LOWER(user_email))
                LOWER(user_email) AS email,
                weight_kg, date
            FROM weight_checkins
            WHERE LOWER(user_email) = ANY($1::text[])
            ORDER BY LOWER(user_email), date DESC
            """,
            emails_lower,
        )

        # ── 3. Food stats 7 zile + logat azi — UN singur GROUP BY ─────────
        food_rows = await conn.fetch(
            """
            SELECT
                LOWER(user_email)         AS email,
                COUNT(DISTINCT date)      AS days_logged,
                COALESCE(AVG(calories),   0)::int AS avg_calories,
                COALESCE(AVG(protein_g),  0)::int AS avg_protein,
                BOOL_OR(date = $2)        AS logged_today
            FROM food_logs
            WHERE LOWER(user_email) = ANY($1::text[])
              AND date >= $3
            GROUP BY LOWER(user_email)
            """,
            emails_lower, today, week_ago,
        )

        # ── 4. Date pentru calcul streak ──────────────────────────────────
        streak_rows = await conn.fetch(
            """
            SELECT LOWER(user_email) AS email, date
            FROM food_logs
            WHERE LOWER(user_email) = ANY($1::text[])
              AND date <= $2
            ORDER BY LOWER(user_email), date DESC
            """,
            emails_lower, today,
        )

    # ── Index by email ────────────────────────────────────────────────────
    sessions_map = {r["email"]: dict(r) for r in sessions_rows}
    checkins_map = {r["email"]: dict(r) for r in checkin_rows}
    food_map     = {r["email"]: dict(r) for r in food_rows}

    # ── Calcul streak per client (O(N log N)) ─────────────────────────────
    streak_map: dict[str, int] = {}
    for email_key, rows in groupby(streak_rows, key=lambda r: r["email"]):
        streak    = 0
        check_day = datetime.date.today()
        for row in rows:
            d = datetime.date.fromisoformat(row["date"])
            if d == check_day or d == check_day - datetime.timedelta(days=1):
                streak   += 1
                check_day = d - datetime.timedelta(days=1)
            else:
                break
        streak_map[email_key] = streak

    # ── Asamblare rezultat final ──────────────────────────────────────────
    results = []
    for email in client_emails:
        ek   = email.lower()
        sess = sessions_map.get(ek, {})
        ci   = checkins_map.get(ek, {})
        food = food_map.get(ek, {})
        streak      = streak_map.get(ek, 0)
        logged_today = bool(food.get("logged_today", False))

        results.append({
            "email":          email,
            "target_kcal":    int(sess["target_kcal"]) if sess.get("target_kcal") else None,
            "protein_g":      int(sess["protein_g"])   if sess.get("protein_g")   else None,
            "goal":           sess.get("goal"),
            "weight_kg":      float(ci["weight_kg"])   if ci.get("weight_kg")     else None,
            "last_checkin":   ci.get("date"),
            "current_streak": streak,
            "logged_today":   logged_today,
            "days_logged_7d": int(food.get("days_logged", 0)),
            "avg_kcal_7d":    int(food.get("avg_calories", 0)),
            "avg_prot_7d":    int(food.get("avg_protein", 0)),
            "alert":          None if logged_today else ("inactiv" if streak == 0 else None),
        })

    return results


# ─────────────────────────────────────────────────────────────────────────────
#  DETALII COMPLET UN CLIENT
# ─────────────────────────────────────────────────────────────────────────────

_DETAIL_SEM = asyncio.Semaphore(5)   # max 5 detalii client simultan


async def _get_client_detail(client_email: str) -> dict:
    """Detalii complete: summary + check-in-uri + food 14 zile + sesiuni."""
    async with _DETAIL_SEM:
        today    = datetime.date.today().isoformat()
        week_ago = (datetime.date.today() - datetime.timedelta(days=14)).isoformat()

        async with get_pool().acquire() as conn:
            # Ultimele 30 check-in-uri
            checkins = await conn.fetch(
                """
                SELECT date, weight_kg FROM weight_checkins
                WHERE LOWER(user_email) = LOWER($1)
                ORDER BY date ASC LIMIT 30
                """,
                client_email,
            )

            # Food logs agregate 14 zile
            food_14d = await conn.fetch(
                """
                SELECT date,
                       SUM(calories)::int  AS calories,
                       SUM(protein_g)::int AS protein_g
                FROM food_logs
                WHERE LOWER(user_email) = LOWER($1) AND date >= $2
                GROUP BY date ORDER BY date ASC
                """,
                client_email, week_ago,
            )

            # Ultimele 3 sesiuni TDEE
            sessions = await conn.fetch(
                """
                SELECT target_kcal, protein_g, carbs_g, fat_g, goal,
                       weight_kg, tdee, timestamp
                FROM sessions
                WHERE LOWER(user_email) = LOWER($1)
                ORDER BY timestamp DESC LIMIT 3
                """,
                client_email,
            )

            # Statistici rapide (refolosim date din batch dacă există)
            last_checkin = await conn.fetchrow(
                """
                SELECT weight_kg, date FROM weight_checkins
                WHERE LOWER(user_email) = LOWER($1)
                ORDER BY date DESC LIMIT 1
                """,
                client_email,
            )
            last_session = await conn.fetchrow(
                """
                SELECT target_kcal, protein_g, goal
                FROM sessions WHERE LOWER(user_email) = LOWER($1)
                ORDER BY timestamp DESC LIMIT 1
                """,
                client_email,
            )
            logged_today = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM food_logs
                    WHERE LOWER(user_email)=LOWER($1) AND date=$2
                )
                """,
                client_email, today,
            )

        # Calcul streak
        streak_rows = await conn.fetch(
            """
            SELECT date FROM food_logs
            WHERE LOWER(user_email)=LOWER($1) AND date <= $2
            ORDER BY date DESC LIMIT 60
            """,
            client_email, today,
        ) if False else []   # calculat mai jos separat

        # Streak simplu
        async with get_pool().acquire() as conn2:
            sr = await conn2.fetch(
                """
                SELECT DISTINCT date FROM food_logs
                WHERE LOWER(user_email)=LOWER($1) AND date <= $2
                ORDER BY date DESC LIMIT 60
                """,
                client_email, today,
            )
        streak    = 0
        check_day = datetime.date.today()
        for row in sr:
            d = datetime.date.fromisoformat(row["date"])
            if d == check_day or d == check_day - datetime.timedelta(days=1):
                streak   += 1
                check_day = d - datetime.timedelta(days=1)
            else:
                break

        return {
            "email":          client_email,
            "target_kcal":    int(last_session["target_kcal"]) if last_session else None,
            "protein_g":      int(last_session["protein_g"])   if last_session else None,
            "goal":           last_session["goal"]              if last_session else None,
            "weight_kg":      float(last_checkin["weight_kg"]) if last_checkin else None,
            "last_checkin":   last_checkin["date"]              if last_checkin else None,
            "current_streak": streak,
            "logged_today":   bool(logged_today),
            "checkins":       [dict(r) for r in checkins],
            "food_14d":       [dict(r) for r in food_14d],
            "sessions":       [dict(r) for r in sessions],
        }


# ─────────────────────────────────────────────────────────────────────────────
#  ROUTER FACTORY
# ─────────────────────────────────────────────────────────────────────────────

def init_nutritionist_router() -> APIRouter:
    router = APIRouter(prefix="/nutritionist", tags=["Nutritionist B2B"])

    @router.post("/register", status_code=201)
    async def register(
        req:   NutritionistRegister,
        email: str = Depends(require_user_email),
    ):
        existing = await get_nutritionist(email)
        if existing:
            return existing
        try:
            nutri = await create_nutritionist(
                email=email, name=req.name, business_name=req.business_name,
            )
        except ValueError as e:
            raise HTTPException(409, str(e))
        app_url = os.getenv("APP_URL", "https://noianlab.ro")
        nutri["invite_link"] = f"{app_url}/?ref_nut={nutri['invite_code']}"
        return nutri

    @router.get("/me")
    async def get_me(email: str = Depends(require_nutritionist)):
        nutri         = await get_nutritionist(email)
        client_emails = await get_nutritionist_clients(email)
        app_url       = os.getenv("APP_URL", "https://noianlab.ro")

        trial_left = None
        if nutri.get("plan_status") == "trial" and nutri.get("trial_ends_at"):
            try:
                ends      = datetime.datetime.fromisoformat(nutri["trial_ends_at"])
                trial_left = max(0, (ends - datetime.datetime.now()).days)
            except Exception:
                pass

        return {
            **nutri,
            "clients_count":   len(client_emails),
            "invite_link":     f"{app_url}/?ref_nut={nutri['invite_code']}",
            "trial_days_left": trial_left,
        }

    @router.put("/me")
    async def update_me(
        req:   NutritionistUpdate,
        email: str = Depends(require_nutritionist),
    ):
        await update_nutritionist_profile(
            email, name=req.name, business_name=req.business_name
        )
        return {"ok": True}

    @router.get("/clients")
    async def my_clients(email: str = Depends(require_nutritionist)):
        """
        Lista clienților cu statistici.
        Folosește batch queries — 4 queries totale indiferent de N.
        """
        client_emails = await get_nutritionist_clients(email)
        if not client_emails:
            return {"clients": [], "total": 0, "alerts_count": 0}

        summaries = await _get_clients_summary_batch(client_emails)

        # Sortare: fără log azi → streak descrescător
        summaries.sort(key=lambda c: (
            0 if not c.get("logged_today") else 1,
            -(c.get("current_streak") or 0),
        ))

        alerts = sum(1 for c in summaries if not c.get("logged_today"))
        return {
            "clients":      summaries,
            "total":        len(summaries),
            "alerts_count": alerts,
        }

    @router.get("/client/{client_email}")
    async def client_detail(
        client_email: str,
        email:        str = Depends(require_nutritionist),
    ):
        my_clients = await get_nutritionist_clients(email)
        if client_email.lower() not in [c.lower() for c in my_clients]:
            raise HTTPException(404, "Clientul nu este în lista ta.")
        return await _get_client_detail(client_email)

    @router.post("/client/add")
    async def add_client(
        req:   AddClientRequest,
        email: str = Depends(require_nutritionist),
    ):
        async with get_pool().acquire() as conn:
            exists = await conn.fetchval(
                "SELECT id FROM users WHERE LOWER(email)=LOWER($1)",
                req.client_email
            )
        if not exists:
            raise HTTPException(404, "Nu există niciun cont cu acest email.")
        await link_client_to_nutritionist(email, req.client_email)
        return {"ok": True, "client_email": req.client_email}

    @router.delete("/client/{client_email}")
    async def remove_client(
        client_email: str,
        email:        str = Depends(require_nutritionist),
    ):
        await remove_client_from_nutritionist(email, client_email)
        return {"ok": True}

    @router.post("/recommend/{client_email}")
    async def send_rec(
        client_email: str,
        req:          RecommendRequest,
        email:        str = Depends(require_nutritionist),
    ):
        my_clients = await get_nutritionist_clients(email)
        if client_email.lower() not in [c.lower() for c in my_clients]:
            raise HTTPException(403, "Clientul nu este în lista ta.")
        rec_id = await send_recommendation(
            coach_email=email,
            client_email=client_email,
            message=req.message.strip(),
        )
        return {"ok": True, "id": rec_id}

    @router.get("/recommend/{client_email}")
    async def get_recs(
        client_email: str,
        email:        str = Depends(require_nutritionist),
    ):
        recs = await get_recommendations_sent_by_coach(email, client_email)
        return {"recommendations": recs}

    @router.get("/invite-link")
    async def invite_link(email: str = Depends(require_nutritionist)):
        nutri   = await get_nutritionist(email)
        app_url = os.getenv("APP_URL", "https://noianlab.ro")
        return {
            "invite_code": nutri["invite_code"],
            "invite_link": f"{app_url}/?ref_nut={nutri['invite_code']}",
        }

    @router.post("/join/{code}")
    async def join_nutritionist(
        code:  str,
        email: str = Depends(require_user_email),
    ):
        nutri = await get_nutritionist_by_invite_code(code)
        if not nutri:
            raise HTTPException(404, "Cod de invitație invalid.")
        if not nutri.get("is_active"):
            raise HTTPException(403, "Contul nutriționistului nu este activ.")
        await link_client_to_nutritionist(nutri["email"], email)
        return {
            "ok":                 True,
            "nutritionist_name":  nutri.get("name", ""),
            "nutritionist_email": nutri["email"],
        }

    return router