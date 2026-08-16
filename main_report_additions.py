# =============================================================================
#  main_report_additions.py — P7: Report Router
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Adaugă în main.py EXACT 2 linii noi:
#
#  ① La importuri (lângă celelalte main_pX_additions):
#       from main_report_additions import init_report_router
#
#  ② La router registration (după celelalte include_router):
#       app.include_router(init_report_router(groq_client))
#
#  Zero modificări în rest. Zero tabele noi în DB.
# =============================================================================

import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from auth import require_user_email
from premium_guard import require_premium
from pdf_report_generator import (
    generate_weekly_pdf_report,
    GOAL_LABELS,
    _week_range,
)


def init_report_router(groq_client) -> APIRouter:
    """
    Creează și returnează router-ul P7 PDF Reports.
    groq_client capturat în closure — același pattern ca P4/P5/P6.
    Apelat O SINGURĂ DATĂ la pornire din main.py.
    """
    router = APIRouter(prefix="/report", tags=["P7 · Weekly PDF Report"])

    # ── GET /report/weekly ────────────────────────────────────────────────────
    @router.get("/weekly")
    async def weekly_report_pdf(
        week_offset: int = 0,
        email: str = Depends(require_premium),
    ):
        """
        Generează și returnează PDF-ul săptămânal ca fișier descărcabil.

        Query params:
          week_offset : 0=curentă (default), -1=trecută, ..., -12=acum 3 luni

        Response: application/pdf cu Content-Disposition: attachment
        Durată: 4-8s cu AI narrative | 1-2s fără groq_client
        """
        if not (-12 <= week_offset <= 0):
            raise HTTPException(
                status_code=400,
                detail="week_offset trebuie să fie între -12 și 0.",
            )

        try:
            pdf_bytes = await generate_weekly_pdf_report(
                email=email,
                week_offset=week_offset,
                groq_client=groq_client,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Eroare la generarea raportului PDF: {exc}",
            )

        monday, _ = _week_range(week_offset)
        filename  = f"raport_{monday.strftime('%Y_%m_%d')}.pdf"

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length":      str(len(pdf_bytes)),
            },
        )

    # ── GET /report/preview ───────────────────────────────────────────────────
    @router.get("/preview")
    async def report_preview_json(
        week_offset: int = 0,
        email: str = Depends(require_premium),
    ):
        """
        Returnează datele raportului ca JSON — instant, fără generare PDF.
        Util pentru a afișa un preview în UI înainte de download.
        """
        from database_analytics import get_weekly_food_summary
        from database import get_checkins, get_profile, get_user_sessions
        from database_streak import compute_streak

        monday, sunday = _week_range(week_offset)
        food_summary   = await get_weekly_food_summary(email, week_offset=week_offset)
        checkins       = await get_checkins(email, limit=30)
        profile        = await get_profile(email)
        sessions       = await get_user_sessions(email, limit=1)
        streak_data    = await compute_streak(email)
        last_session   = sessions[0] if sessions else None

        targets = None
        if last_session:
            targets = {
                "calories":  int(last_session.get("target_kcal") or 0),
                "protein_g": int(last_session.get("protein_g")   or 0),
                "carbs_g":   int(last_session.get("carbs_g")     or 0),
                "fat_g":     int(last_session.get("fat_g")       or 0),
            }

        goal = (profile or {}).get("goal", "mentinere")

        return {
            "week":          {"start": str(monday), "end": str(sunday)},
            "goal":          goal,
            "goal_label":    GOAL_LABELS.get(goal, goal),
            "food_summary":  food_summary,
            "targets":       targets,
            "streak":        streak_data,
            "checkins_count": len(checkins),
            "last_weight":   checkins[-1].get("weight_kg") if checkins else None,
        }

    return router