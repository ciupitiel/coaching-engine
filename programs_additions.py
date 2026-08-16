# =============================================================================
#  programs_additions.py — Structured Programs · API + Stripe
#  Noian Lab
#  -----------------------------------------------------------------------------
#  GET  /programs           → catalog programe (public, fără auth)
#  GET  /programs/my        → programele achiziționate (auth)
#  POST /programs/{slug}/checkout → Stripe one-time checkout
#  POST /programs/webhook   → Stripe webhook → acordă acces
#  GET  /programs/{slug}/content  → conținut program (doar dacă l-ai cumpărat)
#  PUT  /programs/{id}/admin → admin: setează stripe_price_id și content_url
#
#  Adaugă în main.py:
#    from programs_additions import init_programs_router
#    app.include_router(init_programs_router())
#
#  .env / Render — variabile necesare:
#    STRIPE_PROGRAMS_WEBHOOK_SECRET=whsec_xxx
#    (STRIPE_SECRET_KEY deja există)
#
#  Stripe Dashboard:
#    Creează 3 produse One-Time:
#      · 29.99€ → slug: slabire-30-zile
#      · 49.99€ → slug: masa-musculara-8-saptamani
#      · 69.99€ → slug: recompozitie-12-saptamani
#    Copiază Price ID-urile și setează-le cu PUT /programs/{id}/admin
# =============================================================================

import asyncio
import json
import os

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth              import require_user_email
from database_programs import (
    get_all_programs, get_program_by_slug, get_program_by_id,
    get_user_programs, has_user_program, grant_user_program,
    update_program_stripe_price, update_program_content_url,
)

_STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
_STRIPE_PROG_WH    = os.getenv("STRIPE_PROGRAMS_WEBHOOK_SECRET", "")
_APP_URL           = os.getenv("APP_URL", "https://noianlab.ro").rstrip("/")
_ADMIN_EMAIL       = os.getenv("ADMIN_EMAIL", "").strip().lower()

if _STRIPE_SECRET_KEY:
    stripe.api_key = _STRIPE_SECRET_KEY


class AdminUpdateRequest(BaseModel):
    stripe_price_id: str | None = None
    content_url:     str | None = None


def init_programs_router() -> APIRouter:
    router = APIRouter(prefix="/programs", tags=["Programs"])

    # ── GET /programs — catalog public ────────────────────────────────────────
    @router.get("")
    async def list_programs():
        """Catalog programe disponibile. Fără auth — accesibil oricui."""
        programs = await get_all_programs()
        result = []
        for p in programs:
            # Parsăm features din JSON string
            try:
                features = json.loads(p.get("features", "[]"))
            except Exception:
                features = []
            result.append({
                "id":             p["id"],
                "slug":           p["slug"],
                "name":           p["name"],
                "tagline":        p["tagline"],
                "description":    p["description"],
                "duration_label": p["duration_label"],
                "category":       p["category"],
                "price_eur":      float(p["price_eur"]),
                "features":       features,
                "has_stripe":     bool(p.get("stripe_price_id")),
            })
        return {"programs": result}

    # ── GET /programs/my ─────────────────────────────────────────────────────
    @router.get("/my")
    async def my_programs(email: str = Depends(require_user_email)):
        """Programele achiziționate de userul curent."""
        programs = await get_user_programs(email)
        result = []
        for p in programs:
            try:
                features = json.loads(p.get("features", "[]"))
            except Exception:
                features = []
            result.append({
                "id":             p["id"],
                "slug":           p["slug"],
                "name":           p["name"],
                "tagline":        p["tagline"],
                "duration_label": p["duration_label"],
                "price_eur":      float(p["price_eur"]),
                "features":       features,
                "purchased_at":   p["purchased_at"],
                "has_content":    bool(p.get("content_url")),
                "content_url":    p.get("content_url", ""),
            })
        return {"programs": result, "count": len(result)}

    # ── POST /programs/{slug}/checkout ────────────────────────────────────────
    @router.post("/{slug}/checkout")
    async def program_checkout(
        slug:  str,
        email: str = Depends(require_user_email),
    ):
        """
        Creează sesiune Stripe Checkout pentru achiziție one-time.
        Dacă programul nu are stripe_price_id setat → eroare cu instrucțiuni.
        """
        if not _STRIPE_SECRET_KEY:
            raise HTTPException(503, "Stripe neconfiguarat.")

        program = await get_program_by_slug(slug)
        if not program:
            raise HTTPException(404, f"Programul '{slug}' nu există.")

        # Verificăm dacă userul îl are deja
        if await has_user_program(email, program["id"]):
            return {"already_owned": True, "content_url": program.get("content_url", "")}

        price_id = program.get("stripe_price_id", "")
        if not price_id:
            raise HTTPException(
                503,
                f"Programul '{program['name']}' nu are Price ID Stripe configurat. "
                f"Admin: accesează PUT /programs/{program['id']}/admin pentru a seta stripe_price_id."
            )

        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="payment",                    # one-time, NU subscription
            allow_promotion_codes=True,
            customer_email=email,
            success_url=f"{_APP_URL}/?program=success&slug={slug}",
            cancel_url=f"{_APP_URL}/?program=cancel",
            metadata={
                "user_email":  email,
                "program_id":  str(program["id"]),
                "program_slug": slug,
            },
        )
        print(f"💳 Program checkout: {email} → {program['name']} ({program['price_eur']}€)")
        return {"checkout_url": session.url}

    # ── GET /programs/{slug}/content ──────────────────────────────────────────
    @router.get("/{slug}/content")
    async def program_content(
        slug:  str,
        email: str = Depends(require_user_email),
    ):
        """
        Returnează URL-ul conținutului dacă userul a cumpărat programul.
        Conținutul e hostat extern (Google Drive PDF, Notion, etc.).
        """
        program = await get_program_by_slug(slug)
        if not program:
            raise HTTPException(404, "Programul nu există.")

        owned = await has_user_program(email, program["id"])
        if not owned:
            raise HTTPException(
                402,
                {
                    "message": f"Nu ai achiziționat '{program['name']}'.",
                    "price_eur": float(program["price_eur"]),
                    "slug": slug,
                }
            )

        content_url = program.get("content_url", "")
        if not content_url:
            return {
                "ok":         True,
                "content_url": None,
                "message":    "Conținutul programului va fi disponibil în curând. Verifică email-ul.",
            }

        return {
            "ok":          True,
            "content_url": content_url,
            "name":        program["name"],
        }

    # ── POST /programs/webhook — Stripe Events ────────────────────────────────
    @router.post("/webhook", include_in_schema=False)
    async def programs_webhook(request: Request):
        """
        Stripe Dashboard → Webhooks → Add endpoint:
          URL: https://domeniu.tău/programs/webhook
          Events: checkout.session.completed
                  payment_intent.payment_failed

        La checkout.session.completed → acordă automat accesul la program.
        """
        payload    = await request.body()
        sig_header = request.headers.get("stripe-signature", "")

        if _STRIPE_PROG_WH:
            try:
                event = stripe.Webhook.construct_event(
                    payload, sig_header, _STRIPE_PROG_WH
                )
            except stripe.error.SignatureVerificationError:
                raise HTTPException(400, "Semnătură webhook invalidă.")
        else:
            import json as _json
            event = _json.loads(payload)

        etype = event["type"]
        data  = event["data"]["object"]
        print(f"🔔 Programs webhook: {etype}")

        if etype == "checkout.session.completed":
            meta       = data.get("metadata", {})
            user_email = meta.get("user_email", "")
            program_id = meta.get("program_id", "")
            session_id = data.get("id", "")

            if user_email and program_id:
                try:
                    await grant_user_program(
                        email=user_email,
                        program_id=int(program_id),
                        stripe_session_id=session_id,
                    )
                    program = await get_program_by_id(int(program_id))
                    print(f"✅ Program acordat: {user_email} → {program['name'] if program else program_id}")
                except Exception as e:
                    print(f"⚠️ grant_user_program error: {e}")

        return {"ok": True}

    # ── PUT /programs/{id}/admin ───────────────────────────────────────────────
    @router.put("/{program_id}/admin")
    async def admin_update_program(
        program_id: int,
        req:        AdminUpdateRequest,
        email:      str = Depends(require_user_email),
    ):
        """
        Admin-only: setează stripe_price_id și/sau content_url pentru un program.

        Exemplu:
          PUT /programs/1/admin
          {"stripe_price_id": "price_xxx", "content_url": "https://drive.google.com/..."}
        """
        if not _ADMIN_EMAIL or email.lower() != _ADMIN_EMAIL:
            raise HTTPException(403, "Acces restricționat — doar admin.")

        if req.stripe_price_id is not None:
            await update_program_stripe_price(program_id, req.stripe_price_id)
        if req.content_url is not None:
            await update_program_content_url(program_id, req.content_url)

        program = await get_program_by_id(program_id)
        if not program:
            raise HTTPException(404, "Programul nu există.")

        return {
            "ok":             True,
            "id":             program["id"],
            "name":           program["name"],
            "stripe_price_id":program.get("stripe_price_id"),
            "content_url":    program.get("content_url"),
        }

    return router