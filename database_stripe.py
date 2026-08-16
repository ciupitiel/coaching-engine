# =============================================================================
#  database_stripe.py — #17: Stripe Monetizare · DB Layer
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Modul NOU. Nu modifică niciun fișier existent.
#  Adaugă 5 coloane la tabelul users existent prin ALTER TABLE idempotent.
#
#  Integrare în main.py (4 linii — detalii în main_stripe_additions.py):
#    ① Import: from database_stripe import init_db_stripe
#    ② lifespan(): await init_db_stripe()  ← după await init_db_exercise()
#    ③ Import: from main_stripe_additions import init_stripe_router
#    ④ Router: app.include_router(init_stripe_router())
#
#  Coloane adăugate la `users`:
#    · is_premium          → bool, DEFAULT FALSE
#    · stripe_customer_id  → TEXT, ID-ul clientului în Stripe (cus_xxx)
#    · subscription_status → TEXT, statusul: none/active/trialing/past_due/cancelled
#    · subscription_id     → TEXT, ID-ul subscripției (sub_xxx)
#    · premium_until       → TEXT, data expirării (ISO string UTC)
#
#  Funcții publice:
#    init_db_stripe()                        → migrare idempotentă, din lifespan()
#    get_user_subscription(email)            → dict complet status premium
#    update_subscription(email, ...)         → actualizare status după webhook
#    find_email_by_customer(customer_id)     → email după stripe_customer_id
#    set_customer_id(email, customer_id)     → salvare customer ID la checkout
# =============================================================================

import datetime
from database import get_pool


# ─────────────────────────────────────────────────────────────────────────────
#  MIGRARE COLOANE — ALTER TABLE idempotent (IF NOT EXISTS)
#  DEFAULT conservatoare → userii existenți rămân FREE fără impact
# ─────────────────────────────────────────────────────────────────────────────

_MIGRATION_STATEMENTS = [
    # Bool principal — verificat la fiecare request protejat de require_premium
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN NOT NULL DEFAULT FALSE",

    # ID-ul clientului în Stripe — persistent, nu se schimbă la reabonare
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT",

    # Statusul subscripției: none / active / trialing / past_due / cancelled / unpaid
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status TEXT NOT NULL DEFAULT 'none'",

    # ID-ul subscripției Stripe curente (sub_xxx) — necesar pentru portal + webhooks
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_id TEXT",

    # Data expirării perioadei curente (ISO UTC string)
    # Util pentru UI: "Premium până la 15 Aug 2025"
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_until TEXT",
]


# ─────────────────────────────────────────────────────────────────────────────
#  INIȚIALIZARE
# ─────────────────────────────────────────────────────────────────────────────

async def init_db_stripe() -> None:
    """
    Adaugă coloanele Stripe la tabelul users dacă nu există. Idempotent.
    Apelat din lifespan() în main.py, după await init_db_exercise().

    ALTER TABLE cu IF NOT EXISTS este instant în PostgreSQL pentru ADD COLUMN
    cu DEFAULT literal — nu blochează tabelul, zero downtime.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        for stmt in _MIGRATION_STATEMENTS:
            await conn.execute(stmt)
    print("✅  users: is_premium + stripe_customer_id + subscription_status + subscription_id + premium_until — OK")


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def get_user_subscription(email: str) -> dict:
    """
    Returnează statusul complet al subscripției unui user.

    Fallback sigur: dacă emailul nu există (caz rar) returnează
    un dict cu is_premium=False — nu aruncă excepție.

    Apelat din premium_guard.require_premium() la fiecare request protejat.
    Query simplu pe indexul existent de pe coloana email → sub-milisecundă.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT is_premium, stripe_customer_id, subscription_status,
                   subscription_id, premium_until
            FROM users
            WHERE LOWER(email) = LOWER($1)
            """,
            email,
        )
    if not row:
        return {
            "is_premium":          False,
            "stripe_customer_id":  None,
            "subscription_status": "none",
            "subscription_id":     None,
            "premium_until":       None,
        }
    return dict(row)


async def update_subscription(
    email:               str,
    is_premium:          bool,
    stripe_customer_id:  str | None = None,
    subscription_status: str        = "none",
    subscription_id:     str | None = None,
    premium_until:       str | None = None,
) -> None:
    """
    Actualizează statusul subscripției unui user.

    Apelat EXCLUSIV din webhook-urile Stripe (main_stripe_additions.py).
    COALESCE($n, col) → nu suprascrie cu NULL dacă parametrul lipsește.
    Aceasta permite update-uri parțiale (ex: doar is_premium fără să pierdem customer_id).

    Statusuri valide:
        active      → subscripție activă, plată reușită
        trialing    → în perioada de trial (dacă oferi trial)
        past_due    → plata a eșuat, în grace period Stripe (3-7 zile)
        cancelled   → user a anulat subscripția (expiră la period_end)
        unpaid      → grace period expirat, accesul revoctat
        none        → niciodată abonat
    """
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE users SET
                is_premium           = $2,
                stripe_customer_id   = COALESCE($3, stripe_customer_id),
                subscription_status  = $4,
                subscription_id      = COALESCE($5, subscription_id),
                premium_until        = COALESCE($6, premium_until)
            WHERE LOWER(email) = LOWER($1)
            """,
            email.lower(),
            is_premium,
            stripe_customer_id,
            subscription_status,
            subscription_id,
            premium_until,
        )


async def find_email_by_customer(customer_id: str) -> str | None:
    """
    Găsește emailul unui user după stripe_customer_id.

    Apelat din webhook-urile Stripe care nu includ emailul direct —
    Stripe trimite customer ID, noi facem reverse-lookup.
    Returnează None dacă customer_id nu există în DB (customer creat extern).
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email FROM users WHERE stripe_customer_id = $1",
            customer_id,
        )
    return row["email"] if row else None


async def set_customer_id(email: str, customer_id: str) -> None:
    """
    Salvează stripe_customer_id la primul checkout.
    Apelat din /stripe/checkout înainte de a crea sesiunea Stripe.
    Un user are un singur customer ID permanent — reutilizat la reabonare.
    """
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET stripe_customer_id = $2 WHERE LOWER(email) = LOWER($1)",
            email.lower(),
            customer_id,
        )