# =============================================================================
#  nutritionist_stripe.py — Billing Nutriționiști · Stripe
#  Noian Lab · v2 — fix imports la nivel de modul
# =============================================================================

import asyncio
import os

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request

from auth                  import require_user_email
from database              import get_pool
from database_nutritionist import (
    get_nutritionist,
    set_nutritionist_plan_status,
    is_nutritionist,
)

_STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY", "")
_STRIPE_NUTRI_PRICE_ID = os.getenv("STRIPE_NUTRI_PRICE_ID", "")
_STRIPE_NUTRI_WEBHOOK  = os.getenv("STRIPE_NUTRI_WEBHOOK_SECRET", "")
_APP_URL               = os.getenv("APP_URL", "https://noianlab.ro").rstrip("/")

if _STRIPE_SECRET_KEY:
    stripe.api_key = _STRIPE_SECRET_KEY


def _check_config():
    if not _STRIPE_SECRET_KEY:
        raise HTTPException(503, "STRIPE_SECRET_KEY lipsă din .env")
    if not _STRIPE_NUTRI_PRICE_ID:
        raise HTTPException(
            503,
            "STRIPE_NUTRI_PRICE_ID lipsă. Creează produsul în Stripe Dashboard "
            "și adaugă Price ID în .env"
        )


async def _require_nutritionist(email: str = Depends(require_user_email)) -> str:
    if not await is_nutritionist(email):
        raise HTTPException(403, "Cont de nutriționist necesar.")
    return email


# ─────────────────────────────────────────────────────────────────────────────
#  HELPER — caută nutriționist după stripe_customer_id
#  Import get_pool la nivel de modul (fix față de v1)
# ─────────────────────────────────────────────────────────────────────────────

async def _find_nutritionist_by_customer(customer_id: str) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM nutritionists WHERE stripe_customer_id = $1",
            customer_id
        )
    return dict(row) if row else None


def init_nutritionist_stripe_router() -> APIRouter:
    router = APIRouter(prefix="/stripe", tags=["Nutritionist Billing"])

    @router.post("/checkout-nutritionist")
    async def nutri_checkout(email: str = Depends(_require_nutritionist)):
        _check_config()
        nutri = await get_nutritionist(email)
        if not nutri:
            raise HTTPException(404, "Cont de nutriționist negăsit.")
        if nutri.get("plan_status") == "active":
            raise HTTPException(400, "Ești deja abonat. Folosește portalul.")

        customer_id = nutri.get("stripe_customer_id")
        if not customer_id:
            customer    = await asyncio.to_thread(
                stripe.Customer.create,
                email=email,
                name=nutri.get("name", ""),
                metadata={"nutritionist_email": email, "source": "noianlab_nutritionist"},
            )
            customer_id = customer.id
            await set_nutritionist_plan_status(email, "trial", customer_id)

        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": _STRIPE_NUTRI_PRICE_ID, "quantity": 1}],
            mode="subscription",
            allow_promotion_codes=True,
            success_url=f"{_APP_URL}/nutritionist?stripe=success",
            cancel_url=f"{_APP_URL}/nutritionist?stripe=cancel",
            metadata={"nutritionist_email": email},
            subscription_data={"metadata": {"nutritionist_email": email}},
        )
        print(f"💳 Nutritionist checkout: {email}")
        return {"checkout_url": session.url}

    @router.post("/portal-nutritionist")
    async def nutri_portal(email: str = Depends(_require_nutritionist)):
        _check_config()
        nutri       = await get_nutritionist(email)
        customer_id = nutri.get("stripe_customer_id") if nutri else None
        if not customer_id:
            raise HTTPException(400, "Nu ai trecut prin checkout. Activează mai întâi.")
        portal = await asyncio.to_thread(
            stripe.billing_portal.Session.create,
            customer=customer_id,
            return_url=f"{_APP_URL}/nutritionist",
        )
        return {"portal_url": portal.url}

    @router.post("/webhook-nutritionist", include_in_schema=False)
    async def nutri_webhook(request: Request):
        """
        Stripe Dashboard → Webhooks → Add endpoint:
          URL: https://domeniu.tău/stripe/webhook-nutritionist
          Events: checkout.session.completed
                  customer.subscription.updated
                  customer.subscription.deleted
                  invoice.payment_failed
        """
        import json as _json

        payload    = await request.body()
        sig_header = request.headers.get("stripe-signature", "")

        if not _STRIPE_NUTRI_WEBHOOK:
            event = _json.loads(payload)
        else:
            try:
                event = stripe.Webhook.construct_event(
                    payload, sig_header, _STRIPE_NUTRI_WEBHOOK
                )
            except stripe.error.SignatureVerificationError:
                raise HTTPException(400, "Semnătură webhook invalidă.")

        etype = event["type"]
        data  = event["data"]["object"]
        print(f"🔔 Nutritionist webhook: {etype}")

        if etype == "checkout.session.completed":
            nutri_email = (
                data.get("metadata", {}).get("nutritionist_email") or
                data.get("customer_email", "")
            )
            if nutri_email:
                await set_nutritionist_plan_status(
                    nutri_email, "active", data.get("customer", "")
                )
                print(f"✅ Nutritionist activat: {nutri_email}")

        elif etype == "customer.subscription.updated":
            nutri_email = data.get("metadata", {}).get("nutritionist_email", "")
            if nutri_email:
                status = data.get("status", "")
                plan   = "active" if status in ("active", "trialing") else "cancelled"
                await set_nutritionist_plan_status(nutri_email, plan)

        elif etype == "customer.subscription.deleted":
            nutri_email = data.get("metadata", {}).get("nutritionist_email", "")
            if nutri_email:
                await set_nutritionist_plan_status(nutri_email, "cancelled")
                print(f"❌ Nutritionist dezactivat: {nutri_email}")

        elif etype == "invoice.payment_failed":
            customer_id = data.get("customer", "")
            if customer_id:
                nutri = await _find_nutritionist_by_customer(customer_id)
                if nutri:
                    await set_nutritionist_plan_status(nutri["email"], "past_due")
                    print(f"⚠️ Payment failed: {nutri['email']}")

        return {"ok": True}

    return router