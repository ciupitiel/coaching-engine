from fastapi import APIRouter, Depends
from auth import require_user_email
from database_streak import compute_streak


def init_streak_router() -> APIRouter:
    """
    Creează și returnează router-ul E8 pentru streak & gamification.
    Apelat O SINGURĂ DATĂ la pornirea serverului din main.py:
        app.include_router(init_streak_router())
    """
    router = APIRouter(prefix='/streak', tags=['E8 · Streak & Gamification'])

    @router.get('')
    async def streak_get(email: str = Depends(require_user_email)):
        
        return await compute_streak(email)

    return router