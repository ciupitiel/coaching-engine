import os
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field

from auth import require_user_email
from database_push import (
    save_push_subscription,
    delete_push_subscription,
    get_user_subscriptions,
)
from push_engine import (
    VAPID_PUBLIC_KEY,
    send_push_notification,
    send_daily_reminders,
)


# ─────────────────────────────────────────────────────────────────────────────
#  MODELE PYDANTIC
# ─────────────────────────────────────────────────────────────────────────────

class PushSubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=10)
    p256dh:   str = Field(..., min_length=10)
    auth_key: str = Field(..., min_length=4, alias="auth")

    model_config = {"populate_by_name": True}


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=10)


class PushTestRequest(BaseModel):
    endpoint: str | None = Field(
        default=None,
        description="Endpoint specific (optional). Dacă lipsește → primul subscription al userului."
    )


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY ROUTER
# ─────────────────────────────────────────────────────────────────────────────

def init_push_router() -> APIRouter:
    """
    Creează și returnează router-ul Push Notifications.
    Apelat O SINGURĂ DATĂ la pornirea serverului din main.py:
        app.include_router(init_push_router())
    """
    router = APIRouter(prefix="/push", tags=["Push Notifications"])

    # ── GET /push/vapid-key ────────────────────────────────────────────────────
    @router.get("/vapid-key")
    async def get_vapid_key():
        """
        Returnează cheia publică VAPID necesară pentru subscribe în browser.
        Public endpoint — cheia publică VAPID nu e un secret.

        JS-ul din frontend o folosește astfel:
            const reg = await navigator.serviceWorker.ready;
            const sub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(vapidPublicKey)
            });
        """
        if not VAPID_PUBLIC_KEY:
            raise HTTPException(
                status_code=503,
                detail="Push notifications nedisponibile — VAPID_PUBLIC_KEY lipsă în .env.",
            )
        return {
            "vapid_public_key": VAPID_PUBLIC_KEY,
            "available": True,
        }

    # ── POST /push/subscribe ──────────────────────────────────────────────────
    @router.post("/subscribe")
    async def push_subscribe(
        req:   PushSubscribeRequest,
        email: str = Depends(require_user_email),
    ):
        """
        Salvează un push subscription nou.

        Flow frontend:
          1. JS cere permisiune: Notification.requestPermission()
          2. JS creează subscription: pushManager.subscribe(...)
          3. JS trimite subscription la acest endpoint
          4. Backend salvează în DB → userul va primi notificări zilnice

        Securitate:
          · UPSERT pe endpoint — același browser re-subscriind nu creează duplicate
          · Emailul din JWT garantează că subscription-ul aparține userului corect
        """
        await save_push_subscription(
            email=email,
            endpoint=req.endpoint,
            p256dh=req.p256dh,
            auth_key=req.auth_key,
        )
        return {
            "ok":      True,
            "message": "Notificările push sunt activate.",
        }

    # ── DELETE /push/unsubscribe ──────────────────────────────────────────────
    @router.delete("/unsubscribe")
    async def push_unsubscribe(
        req:   PushUnsubscribeRequest,
        email: str = Depends(require_user_email),
    ):
        """
        Șterge un subscription (userul dezactivează notificările din Settings).
        Browserul trebuie să apeleze pushManager.unsubscribe() înainte sau după.
        """
        deleted = await delete_push_subscription(email=email, endpoint=req.endpoint)
        return {
            "ok":      True,
            "deleted": deleted,
            "message": "Notificările push sunt dezactivate.",
        }

    # ── GET /push/status ──────────────────────────────────────────────────────
    @router.get("/status")
    async def push_status(email: str = Depends(require_user_email)):
        """
        Returnează subscriptions-urile active ale userului.
        Frontend-ul verifică dacă există subscriptions pentru a afișa starea toggle-ului.

        Response:
            {
                "subscribed": true,
                "count": 1,
                "subscriptions": [{"endpoint": "...", "created_at": "..."}]
            }
        """
        subs = await get_user_subscriptions(email)
        # Nu trimitem p256dh și auth_key la client — informație sensibilă inutilă pentru UI
        safe_subs = [
            {"endpoint": s["endpoint"][:60] + "...", "created_at": s["created_at"]}
            for s in subs
        ]
        return {
            "subscribed":    len(subs) > 0,
            "count":         len(subs),
            "subscriptions": safe_subs,
        }

    # ── POST /push/test ───────────────────────────────────────────────────────
    @router.post("/test")
    async def push_test(
        req:   PushTestRequest,
        email: str = Depends(require_user_email),
    ):
        """
        Trimite o notificare push de test userului autentificat.
        Util pentru a verifica că totul funcționează înainte de lansare.

        Dacă endpoint lipsește → folosim primul subscription al userului.
        """
        subs = await get_user_subscriptions(email)

        if not subs:
            raise HTTPException(
                status_code=400,
                detail="Niciun subscription activ. Activează notificările din Settings mai întâi.",
            )

        # Găsim subscription-ul cerut sau folosim primul
        target = None
        if req.endpoint:
            target = next((s for s in subs if s["endpoint"] == req.endpoint), None)
        if not target:
            target = subs[0]

        payload = {
            "title": "✅ Test Notificare · Coaching Engine",
            "body":  "Notificările push funcționează corect! Vei fi reamintit zilnic la 20:00.",
            "tag":   "push-test",
            "url":   "/",
        }

        ok = await send_push_notification(
            endpoint=target["endpoint"],
            p256dh=target["p256dh"],
            auth_key=target["auth_key"],
            payload=payload,
        )

        if not ok:
            raise HTTPException(
                status_code=500,
                detail="Trimiterea notificării a eșuat. Verifică că notificările sunt permise în browser.",
            )

        return {
            "ok":      True,
            "message": "Notificare test trimisă cu succes.",
        }

    # ── POST /push/trigger-daily ──────────────────────────────────────────────
    @router.post("/trigger-daily", include_in_schema=False)
    async def push_trigger_daily(
        x_admin_secret: str | None = Header(default=None, alias="X-Admin-Secret"),
    ):
        """
        Declanșează manual job-ul de reminder zilnic.
        Protejat cu ADMIN_SECRET din .env — nu e afișat în Swagger.
        Util pentru a testa reminder-ul fără a aștepta 20:00.

        Curl test:
            curl -X POST http://localhost:8000/push/trigger-daily \
                 -H "X-Admin-Secret: secretul_tau_din_env"
        """
        admin_secret = os.getenv("ADMIN_SECRET", "")
        if not admin_secret or x_admin_secret != admin_secret:
            raise HTTPException(status_code=403, detail="Acces interzis.")

        await send_daily_reminders()
        return {"ok": True, "message": "Job zilnic declanșat manual."}

    return router