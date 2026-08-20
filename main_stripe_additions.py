# =============================================================================
#  main_stripe_additions.py — #17: Stripe Monetizare · Router
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Adaugă în main.py EXACT 4 linii:
#
#  ① La importuri (după `from database_exercise import init_db_exercise`):
#       from database_stripe import init_db_stripe
#       from main_stripe_additions import init_stripe_router
#
#  ② În lifespan(), DUPĂ `await init_db_exercise()`:
#       await init_db_stripe()
#
#  ③ La routers (după `app.include_router(init_exercise_router(groq_client))`):
#       app.include_router(init_stripe_router())
#
#  Total modificări în main.py: 4 linii noi. Zero înlocuiri.
#  -----------------------------------------------------------------------------
#  Variabile .env necesare (adaugă toate 4):
#    STRIPE_SECRET_KEY=sk_test_xxx       → din Stripe Dashboard → API Keys
#    STRIPE_WEBHOOK_SECRET=whsec_xxx     → din Stripe Dashboard → Webhooks
#    STRIPE_PRICE_ID=price_xxx           → din Stripe Dashboard → Products
#    STRIPE_PORTAL_CONFIG_ID=bpc_xxx     → opțional (Customer Portal configuration)
#
#  Endpoint-uri expuse:
#    GET  /stripe/status          → statusul subscripției userului curent
#    POST /stripe/checkout        → creează sesiune checkout → returnează URL
#    POST /stripe/portal          → accesează Customer Portal Stripe
#    POST /stripe/webhook         → handler webhook (neautentificat — Stripe semnează)
#
#  Webhook events gestionate:
#    checkout.session.completed      → activează Premium
#    customer.subscription.updated   → actualizează status (renewals, upgrades, downgrades)
#    customer.subscription.deleted   → dezactivează Premium (anulare imediată)
#    invoice.payment_failed          → marchează past_due (Stripe gestionează grace period)
#    invoice.payment_succeeded       → confirmă Premium activ (renewal reușit)
#
#  Flow complet user:
#    1. GET /stripe/status → "free"
#    2. POST /stripe/checkout → {checkout_url}
#    3. Redirect la Stripe hosted page → completează plata
#    4. Stripe → POST /stripe/webhook (checkout.session.completed)
#    5. Backend → is_premium=True
#    6. Frontend redirect la /?stripe=success
#    7. GET /stripe/status → "active"
#
#  Securitate webhook:
#    · Stripe semnează fiecare request cu STRIPE_WEBHOOK_SECRET (HMAC-SHA256)
#    · stripe.Webhook.construct_event() verifică semnătura → respinge requests false
#    · Endpoint fără autentificare JWT (Stripe nu are token) dar cu semnătură proprie
#    · Body-ul raw (bytes) e obligatoriu — nu parsăm JSON înainte de verificare
# =============================================================================

import os
import datetime
import asyncio
import stripe

from fastapi import APIRouter, Depends, HTTPException, Request
from database import get_pool
from auth import require_user_email
from database_stripe import (
    find_email_by_customer,
    get_user_subscription,
    set_customer_id,
    update_subscription,
)


# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURARE
# ─────────────────────────────────────────────────────────────────────────────

_STRIPE_SECRET_KEY      = os.getenv("STRIPE_SECRET_KEY",      "")
_STRIPE_WEBHOOK_SECRET  = os.getenv("STRIPE_WEBHOOK_SECRET",  "")
_STRIPE_PRICE_ID        = os.getenv("STRIPE_PRICE_ID",        "")
_STRIPE_PORTAL_CONFIG   = os.getenv("STRIPE_PORTAL_CONFIG_ID", "")  # optional

_APP_URL = os.getenv("APP_URL", "http://localhost:8000").rstrip("/")

# Stripe API key globală — setată O dată la import
if _STRIPE_SECRET_KEY:
    stripe.api_key = _STRIPE_SECRET_KEY


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITARE INTERNE
# ─────────────────────────────────────────────────────────────────────────────

def _check_stripe_config() -> None:
    """Verifică că variabilele Stripe sunt setate. Aruncă 503 dacă nu."""
    if not _STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "Stripe neconfigurat — adaugă STRIPE_SECRET_KEY în .env și Render Dashboard. "
                "Generează cheia de la: https://dashboard.stripe.com/apikeys"
            ),
        )
    if not _STRIPE_PRICE_ID:
        raise HTTPException(
            status_code=503,
            detail=(
                "STRIPE_PRICE_ID lipsă — creează un produs Subscripție în Stripe Dashboard "
                "și copiază Price ID-ul (price_xxx) în .env"
            ),
        )


def _unix_to_iso(ts: int | None) -> str | None:
    """Convertește Unix timestamp Stripe → ISO string UTC."""
    if not ts:
        return None
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


def _is_premium_status(status: str) -> bool:
    """
    Determină dacă un status Stripe înseamnă acces Premium activ.
    active + trialing = Premium. Orice altceva = free.
    past_due = Stripe gestionează grace period (3-7 zile), dar noi
    retrogradăm imediat pentru a evita abuzurile.
    """
    return status in ("active", "trialing")


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY ROUTER
# ─────────────────────────────────────────────────────────────────────────────

def init_stripe_router() -> APIRouter:
    """
    Creează și returnează router-ul #17 Stripe.
    Apelat O SINGURĂ DATĂ la pornire din main.py:
        app.include_router(init_stripe_router())
    """
    router = APIRouter(prefix="/stripe", tags=["#17 · Stripe Monetizare"])

    # ── GET /stripe/status ─────────────────────────────────────────────────────
    @router.get("/status")
    async def stripe_status(email: str = Depends(require_user_email)):
        """
        Returnează statusul complet al subscripției userului autentificat.

        Response:
            {
                "is_premium":          true,
                "subscription_status": "active",
                "premium_until":       "2025-09-01T00:00:00+00:00",
                "stripe_customer_id":  "cus_xxx",
                "can_manage":          true      ← are Customer Portal disponibil
            }

        Apelat la:
          · Deschiderea secțiunii Settings / Premium din UI
          · Redirect după /?stripe=success pentru confirmare imediată
          · La login pentru a afișa badge-ul Premium în header
        """
        sub = await get_user_subscription(email)
        return {
            "is_premium":          bool(sub.get("is_premium", False)),
            "subscription_status": sub.get("subscription_status", "none"),
            "premium_until":       sub.get("premium_until"),
            "stripe_customer_id":  sub.get("stripe_customer_id"),
            "can_manage":          bool(sub.get("stripe_customer_id")),
        }

    # ── POST /stripe/checkout ──────────────────────────────────────────────────
    @router.post("/checkout")
    async def stripe_checkout(email: str = Depends(require_user_email)):
        """
        Creează o sesiune de checkout Stripe și returnează URL-ul.

        Frontend redirect:
            const res = await fetch('/stripe/checkout', {method: 'POST', headers: {Authorization: ...}});
            const {checkout_url} = await res.json();
            window.location.href = checkout_url;

        Stripe hosted page → user completează plata → Stripe webhook →
        is_premium=True → redirect la {APP_URL}/?stripe=success

        Customer ID reutilizat: dacă userul a mai fost abonat, refolosim
        customer-ul Stripe existent → istoricul facturilor e păstrat.
        """
        _check_stripe_config()

        sub = await get_user_subscription(email)

        if bool(sub.get("is_premium", False)):
            raise HTTPException(
                status_code=400,
                detail="Ești deja abonat Premium. Accesează portalul pentru a-ți gestiona subscripția.",
            )

        # ── Obține sau creează Customer Stripe ────────────────────────────
        customer_id = sub.get("stripe_customer_id")

        if not customer_id:
            # Prima dată — creăm customer cu emailul userului
            customer = await asyncio.to_thread(
                stripe.Customer.create,
                email=email,
                metadata={"app_email": email, "source": "coaching_engine"},
            )
            customer_id = customer.id
            await set_customer_id(email, customer_id)

        # ── Creează sesiunea de checkout ──────────────────────────────────
        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": _STRIPE_PRICE_ID, "quantity": 1}],
            mode="subscription",
            allow_promotion_codes=True,           # permite coduri promoționale
            success_url=f"{_APP_URL}/?stripe=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{_APP_URL}/?stripe=cancel",
            metadata={"user_email": email},        # necesar în webhook pentru lookup
            subscription_data={
                "metadata": {"user_email": email}  # backup lookup în sub events
            },
        )

        print(f"💳  Checkout creat: {email} → {session.id}")

        return {
            "checkout_url": session.url,
            "session_id":   session.id,
        }

    # ── POST /stripe/portal ────────────────────────────────────────────────────
    @router.post("/portal")
    async def stripe_portal(email: str = Depends(require_user_email)):
        """
        Creează o sesiune Customer Portal și returnează URL-ul.

        Customer Portal = pagina Stripe unde userul poate:
          · Anula subscripția
          · Schimba metoda de plată
          · Descărca facturile
          · Reactiva o subscripție anulată

        Fără Portal, ai nevoie de cod custom pentru toate astea.
        Cu Portal, Stripe gestionează totul → webhooks te informează de schimbări.

        Necesită ca userul să aibă deja un stripe_customer_id (a trecut cel puțin
        o dată prin checkout, chiar dacă n-a finalizat plata).
        """
        _check_stripe_config()

        sub = await get_user_subscription(email)
        customer_id = sub.get("stripe_customer_id")

        if not customer_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Nu există niciun abonament asociat contului tău. "
                    "Pornește un abonament nou din secțiunea Premium."
                ),
            )

        # Construim kwargs pentru portal — configurația e opțională
        portal_kwargs: dict = {
            "customer":   customer_id,
            "return_url": f"{_APP_URL}/?tab=settings",
        }
        if _STRIPE_PORTAL_CONFIG:
            portal_kwargs["configuration"] = _STRIPE_PORTAL_CONFIG

        try:
            session = await asyncio.to_thread(
                stripe.billing_portal.Session.create,
                **portal_kwargs,
            )
        except stripe.InvalidRequestError as e:
            if "No such customer" in str(e):
                new_customer = await asyncio.to_thread(
                    stripe.Customer.create,
                    email=email,
                    metadata={"app": "noian-lab"},
                )
                new_customer_id = new_customer.id
                async with get_pool().acquire() as conn:
                    await conn.execute(
                        "UPDATE users SET stripe_customer_id = $1 WHERE LOWER(email) = LOWER($2)",
                        new_customer_id, email
                    )
                portal_kwargs["customer"] = new_customer_id
                session = await asyncio.to_thread(
                    stripe.billing_portal.Session.create,
                    **portal_kwargs,
                )
            else:
                raise

        return {"portal_url": session.url}

    # ── POST /stripe/webhook ───────────────────────────────────────────────────
    @router.post("/webhook", include_in_schema=False)
    async def stripe_webhook(request: Request):
        """
        Endpoint webhook Stripe — primește și procesează events.

        CRITICĂ: body-ul RAW (bytes) trebuie citit ÎNAINTE de orice parsare.
        Stripe verifică semnătura HMAC-SHA256 pe body-ul original —
        dacă îl parsăm înainte, semnătura nu mai coincide.

        Configurare în Stripe Dashboard:
          Developers → Webhooks → Add endpoint
          URL: https://coaching-engine.onrender.com/stripe/webhook
          Events:
            ✅ checkout.session.completed
            ✅ customer.subscription.updated
            ✅ customer.subscription.deleted
            ✅ invoice.payment_failed
            ✅ invoice.payment_succeeded

        Returnează 200 întotdeauna (chiar și pentru events negestionate) —
        altfel Stripe retrimite în loop exponential.
        """
        if not _STRIPE_WEBHOOK_SECRET:
            raise HTTPException(
                status_code=503,
                detail="STRIPE_WEBHOOK_SECRET lipsă — adaugă în .env",
            )

        payload   = await request.body()
        sig       = request.headers.get("stripe-signature", "")

        try:
            event = await asyncio.to_thread(
                stripe.Webhook.construct_event,
                payload,
                sig,
                _STRIPE_WEBHOOK_SECRET,
            )
        except stripe.SignatureVerificationError:
            print("⚠️  Webhook Stripe: semnătură invalidă — request respins")
            raise HTTPException(status_code=400, detail="Semnătură webhook invalidă.")
        except Exception as exc:
            print(f"⚠️  Webhook Stripe parse error: {exc}")
            raise HTTPException(status_code=400, detail=str(exc))

        etype = event["type"]
        obj   = event["data"]["object"]

        print(f"🔔  Stripe webhook: {etype}")

        # ── checkout.session.completed ────────────────────────────────────
        # Plata inițială reușită → activăm Premium imediat
        if etype == "checkout.session.completed":
            email       = (obj.get("metadata") or {}).get("user_email")
            customer_id = obj.get("customer")
            sub_id      = obj.get("subscription")

            if not email and customer_id:
                email = await find_email_by_customer(customer_id)

            if email:
                # Încercăm să obținem period_end din subscripție
                premium_until = None
                if sub_id:
                    try:
                        sub_obj = await asyncio.to_thread(
                            stripe.Subscription.retrieve, sub_id
                        )
                        premium_until = _unix_to_iso(
                            sub_obj.get("current_period_end")
                        )
                    except Exception:
                        pass

                await update_subscription(
                    email=email,
                    is_premium=True,
                    stripe_customer_id=customer_id,
                    subscription_status="active",
                    subscription_id=sub_id,
                    premium_until=premium_until,
                )
                print(f"✅  Premium ACTIVAT: {email} → sub {sub_id}")
            else:
                print(f"⚠️  checkout.session.completed: email negăsit pentru customer {customer_id}")

        # ── customer.subscription.updated ─────────────────────────────────
        # Renewal lunar, schimbare plan, status change (past_due etc.)
        elif etype == "customer.subscription.updated":
            customer_id  = obj.get("customer")
            sub_status   = obj.get("status", "none")
            sub_id       = obj.get("id")
            period_end   = _unix_to_iso(obj.get("current_period_end"))
            is_premium   = _is_premium_status(sub_status)

            # Lookup email din customer_id (sub events nu au metadata user)
            email = (obj.get("metadata") or {}).get("user_email")
            if not email and customer_id:
                email = await find_email_by_customer(customer_id)

            if email:
                await update_subscription(
                    email=email,
                    is_premium=is_premium,
                    stripe_customer_id=customer_id,
                    subscription_status=sub_status,
                    subscription_id=sub_id,
                    premium_until=period_end if is_premium else None,
                )
                badge = "✅" if is_premium else "⚠️"
                print(f"{badge}  Subscription updated [{sub_status}]: {email}")
            else:
                print(f"⚠️  subscription.updated: email negăsit pentru customer {customer_id}")

        # ── customer.subscription.deleted ─────────────────────────────────
        # Anulare imediată (nu la period_end) sau expirare după anulare
        elif etype == "customer.subscription.deleted":
            customer_id = obj.get("customer")
            sub_id      = obj.get("id")

            email = (obj.get("metadata") or {}).get("user_email")
            if not email and customer_id:
                email = await find_email_by_customer(customer_id)

            if email:
                await update_subscription(
                    email=email,
                    is_premium=False,
                    subscription_status="cancelled",
                    premium_until=None,
                )
                print(f"❌  Premium ANULAT: {email}")
            else:
                print(f"⚠️  subscription.deleted: email negăsit pentru customer {customer_id}")

        # ── invoice.payment_failed ────────────────────────────────────────
        # Plată eșuată → Stripe va reîncerca automat 3-4 zile
        # Noi revocăm accesul imediat (policy strict)
        elif etype == "invoice.payment_failed":
            customer_id = obj.get("customer")

            email = await find_email_by_customer(customer_id) if customer_id else None
            if email:
                await update_subscription(
                    email=email,
                    is_premium=False,
                    subscription_status="past_due",
                    premium_until=None,
                )
                print(f"⚠️  Plată EȘUATĂ → past_due: {email}")

        # ── invoice.payment_succeeded ─────────────────────────────────────
        # Renewal lunar reușit → confirmă Premium activ și actualizează premium_until
        elif etype == "invoice.payment_succeeded":
            customer_id    = obj.get("customer")
            billing_reason = obj.get("billing_reason", "")

            if billing_reason == "subscription_cycle" and customer_id:
                email = await find_email_by_customer(customer_id)
                if email:
                    # Obținem premium_until actualizat din subscripție
                    sub_id    = obj.get("subscription")
                    new_until = None
                    if sub_id:
                        try:
                            sub_obj   = await asyncio.to_thread(stripe.Subscription.retrieve, sub_id)
                            new_until = _unix_to_iso(sub_obj.get("current_period_end"))
                        except Exception:
                            pass

                    await update_subscription(
                        email=email,
                        is_premium=True,
                        subscription_status="active",
                        premium_until=new_until,
                    )
                    print(f"✅  Renewal REUȘIT + premium_until actualizat: {email}")

        # Orice alt event Stripe → ignorăm silențios, returnăm 200
        else:
            print(f"ℹ️  Webhook ignorat: {etype}")

        return {"received": True}

    # ── GET /stripe/config ─────────────────────────────────────────────────────
    @router.get("/config")
    async def stripe_config():
        """
        Returnează configurația publică Stripe pentru frontend.
        Publishable key nu e un secret — poate fi expus în browser.
        """
        publishable = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
        return {
            "publishable_key":  publishable,
            "price_id":         _STRIPE_PRICE_ID,
            "configured":       bool(_STRIPE_SECRET_KEY and _STRIPE_PRICE_ID),
        }

    return router