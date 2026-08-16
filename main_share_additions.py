"""
main_share_additions.py
Endpoint-uri pentru link-uri publice de partajare progres.

Rute (fără prefix):
  POST   /share/generate       → creează/returnează token (auth required)
  GET    /share/me             → returnează token existent sau null (auth required)
  DELETE /share/revoke         → șterge token-ul (auth required)
  GET    /share/{token}/data   → JSON public, fără auth
  GET    /share/{token}        → servește share.html (fără auth)

IMPORTANT: ordinea rutelor contează.
  /share/me, /share/generate, /share/revoke  → înregistrate ÎNAINTE de /share/{token}
  → Starlette potrivește literal înainte de parametrizat.
"""

import asyncio
import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from auth import require_user_email
from database_share import (
    get_or_create_share_token,
    get_share_token_for_user,
    get_email_by_share_token,
    revoke_share_token,
)
from database import get_checkins, get_user_sessions
from database_streak import compute_streak
from database_analytics import get_weekly_food_summary


def init_share_router() -> APIRouter:
    router = APIRouter(tags=["Share · Progres Public"])

    # ── POST /share/generate ──────────────────────────────────────────────────
    @router.post("/share/generate")
    async def generate_share_token(email: str = Depends(require_user_email)):
        """Generează sau returnează token-ul de partajare al userului."""
        token   = await get_or_create_share_token(email)
        app_url = os.getenv("APP_URL", "http://localhost:8000").rstrip("/")
        return {"token": token, "share_url": f"{app_url}/share/{token}"}

    # ── GET /share/me ─────────────────────────────────────────────────────────
    @router.get("/share/me")
    async def get_my_share_token(email: str = Depends(require_user_email)):
        """Returnează token-ul existent al userului, sau null dacă nu există."""
        token   = await get_share_token_for_user(email)
        app_url = os.getenv("APP_URL", "http://localhost:8000").rstrip("/")
        if not token:
            return {"token": None, "share_url": None}
        return {"token": token, "share_url": f"{app_url}/share/{token}"}

    # ── DELETE /share/revoke ──────────────────────────────────────────────────
    @router.delete("/share/revoke")
    async def revoke_share(email: str = Depends(require_user_email)):
        """Revocă link-ul de partajare. Generarea unui nou link e posibilă imediat."""
        await revoke_share_token(email)
        return {"ok": True}

    # ── GET /share/{token}/data ───────────────────────────────────────────────
    @router.get("/share/{token}/data")
    async def get_share_data(token: str):
        """
        Returnează datele publice pentru pagina de partajare.
        Fără autentificare — acces public prin token opac.

        Response:
          display_name : str   (prefix email, capitalizat)
          checkins     : [{date, weight_kg}]  ultimele 90 de zile
          streak       : {current, logged_today}
          compliance   : {pct, avg_kcal, target_kcal, days_logged}
        """
        email = await get_email_by_share_token(token)
        if not email:
            raise HTTPException(status_code=404, detail="Link invalid sau expirat.")

        checkins, streak, weekly, sessions = await asyncio.gather(
            get_checkins(email, limit=90),
            compute_streak(email),
            get_weekly_food_summary(email, week_offset=0),
            get_user_sessions(email, limit=1),
        )

        last_session = sessions[0] if sessions else None
        target_kcal  = last_session.get("target_kcal") if last_session else None
        avg_kcal     = weekly["avg_daily"].get("calories", 0)
        compliance   = (
            min(150, round(avg_kcal / target_kcal * 100))
            if target_kcal and avg_kcal else None
        )

        return {
            "display_name": email.split("@")[0].capitalize(),
            "checkins": [
                {"date": c["date"], "weight_kg": float(c["weight_kg"])}
                for c in checkins
            ],
            "streak": {
                "current":     streak.get("current_streak", 0),
                "logged_today": streak.get("logged_today", False),
            },
            "compliance": {
                "pct":         compliance,
                "avg_kcal":    avg_kcal,
                "target_kcal": target_kcal,
                "days_logged": weekly.get("days_logged", 0),
            },
        }

    # ── GET /share/{token} — servește share.html ──────────────────────────────
    @router.get("/share/{token}", include_in_schema=False)
    async def serve_share_page(token: str):
        if os.path.exists("share.html"):
            return FileResponse("share.html")
        raise HTTPException(status_code=404, detail="share.html lipsă.")

    return router