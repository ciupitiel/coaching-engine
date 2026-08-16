import os
from fastapi import APIRouter, Depends
from auth import require_user_email
from database_referral import get_or_create_referral_code, get_referral_stats


def init_referral_router() -> APIRouter:
    router = APIRouter(tags=["Referral"])

    @router.get("/referral/me")
    async def get_my_referral(email: str = Depends(require_user_email)):
        """
        Returnează codul de referral al userului (generat la prima cerere)
        și statisticile de invitații.

        Response:
          code       : "NC-XXXXXX"
          link       : "https://noianlab.ro/?ref=NC-XXXXXX"
          stats      : {invited, completed, months_earned}
        """
        code    = await get_or_create_referral_code(email)
        app_url = os.getenv("APP_URL", "http://localhost:8000").rstrip("/")
        stats   = await get_referral_stats(email)
        return {
            "code":  code,
            "link":  f"{app_url}/?ref={code}",
            "stats": stats,
        }

    return router