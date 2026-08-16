"""
database_referral.py
Sistem de referral: invită un prieten, amândoi primiți 30 zile Premium.

Tabele:
  referral_codes  — un cod per user, generat la prima cerere
  referrals       — log-ul invitațiilor (pending → completed)

Flow:
  1. User A face GET /referral/me → primește codul + link-ul
  2. User B deschide /?ref=NC-XXXX, se înregistrează
  3. main.py/signup stochează referral ca 'pending'
  4. La verificarea emailului lui B → complete_referral_if_exists(B)
     → ambii primesc 30 zile Premium, referral → 'completed'
"""

import datetime
import secrets
from database import get_pool


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

async def init_db_referral() -> None:
    """Creează tabelele de referral. Idempotent."""
    async with get_pool().acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS referral_codes (
                id         SERIAL  PRIMARY KEY,
                user_email TEXT    NOT NULL UNIQUE,
                code       TEXT    NOT NULL UNIQUE,
                created_at TEXT    NOT NULL
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ref_code ON referral_codes(code)"
        )
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id             SERIAL  PRIMARY KEY,
                referrer_email TEXT    NOT NULL,
                referred_email TEXT    NOT NULL UNIQUE,
                status         TEXT    NOT NULL DEFAULT 'pending',
                created_at     TEXT    NOT NULL,
                completed_at   TEXT
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ref_referrer ON referrals(referrer_email)"
        )
    print("✅  referral_codes + referrals: OK")


# ─────────────────────────────────────────────────────────────────────────────
#  COD REFERRAL
# ─────────────────────────────────────────────────────────────────────────────

def _gen_code() -> str:
    """NC-XXXXXX — 6 caractere alfanumerice uppercase, ușor de citit."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # fără I/O/0/1 — confuzabile
    raw = "".join(secrets.choice(alphabet) for _ in range(6))
    return f"NC-{raw}"


async def get_or_create_referral_code(email: str) -> str:
    """Returnează codul existent sau îl creează. Idempotent."""
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO referral_codes (user_email, code, created_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_email) DO UPDATE SET user_email = EXCLUDED.user_email
            RETURNING code
            """,
            email.lower(),
            _gen_code(),
            now,
        )
    return row["code"]


async def get_referrer_by_code(code: str) -> str | None:
    """Returnează emailul proprietarului codului, sau None dacă nu există."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_email FROM referral_codes WHERE UPPER(code) = UPPER($1)",
            code.strip(),
        )
    return row["user_email"] if row else None


# ─────────────────────────────────────────────────────────────────────────────
#  REFERRAL PENDING
# ─────────────────────────────────────────────────────────────────────────────

async def create_referral_pending(ref_code: str, referred_email: str) -> bool:
    """
    Înregistrează o invitație pendingcând un user se înregistrează cu un cod.

    Returnează False dacă:
      · codul nu există
      · referrer-ul e același cu referred (auto-referral)
      · referred_email a mai fost invitat deja
    """
    referrer = await get_referrer_by_code(ref_code)
    if not referrer:
        return False
    if referrer.lower() == referred_email.lower():
        return False          # nu-ți poți invita propriul cont

    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    async with get_pool().acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO referrals (referrer_email, referred_email, status, created_at)
                VALUES ($1, $2, 'pending', $3)
                ON CONFLICT (referred_email) DO NOTHING
                """,
                referrer.lower(),
                referred_email.lower(),
                now,
            )
        except Exception:
            return False
    print(f"🔗  Referral pending: {referrer} → {referred_email}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  COMPLETE REFERRAL — apelat la verificarea emailului
# ─────────────────────────────────────────────────────────────────────────────

async def complete_referral_if_exists(referred_email: str) -> bool:
    """
    Dacă există un referral pending pentru referred_email:
      · Acordă 30 zile Premium ambilor useri
      · Marchează referral-ul ca 'completed'
      · Trimite email de notificare referrer-ului

    Returncează True dacă s-a completat un referral, False altfel.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT referrer_email FROM referrals
            WHERE LOWER(referred_email) = LOWER($1) AND status = 'pending'
            """,
            referred_email,
        )
    if not row:
        return False

    referrer = row["referrer_email"]

    # Acordă Premium la amândoi
    await _grant_free_premium(referred_email, days=30)
    await _grant_free_premium(referrer,        days=30)

    # Marchează completat
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE referrals
            SET status = 'completed', completed_at = $1
            WHERE LOWER(referred_email) = LOWER($2)
            """,
            now,
            referred_email,
        )

    # Email notificare referrer
    try:
        from email_service import send_referral_reward_email
        await send_referral_reward_email(referrer, referred_email)
    except Exception as e:
        print(f"⚠️  Referral reward email failed: {e}")

    print(f"✅  Referral completat: {referrer} → {referred_email} (+30 zile Premium amândoi)")
    return True


async def _grant_free_premium(email: str, days: int = 30) -> None:
    """Extinde sau activează Premium pentru `days` zile."""
    from database_stripe import get_user_subscription, update_subscription

    sub           = await get_user_subscription(email)
    premium_until = None

    if sub and sub.get("is_premium") and sub.get("premium_until"):
        # Extinde premium_until existent
        try:
            current = datetime.datetime.fromisoformat(sub["premium_until"][:19])
            new_dt  = max(current, datetime.datetime.now()) + datetime.timedelta(days=days)
            premium_until = new_dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass

    if not premium_until:
        premium_until = (
            datetime.datetime.now() + datetime.timedelta(days=days)
        ).strftime("%Y-%m-%dT%H:%M:%S")

    await update_subscription(
        email=email,
        is_premium=True,
        subscription_status="trial",
        premium_until=premium_until,
    )
    print(f"🎁  Premium trial acordat: {email} → {premium_until}")


# ─────────────────────────────────────────────────────────────────────────────
#  STATS
# ─────────────────────────────────────────────────────────────────────────────

async def get_referral_stats(email: str) -> dict:
    """Returnează statisticile de referral ale userului."""
    async with get_pool().acquire() as conn:
        invited = await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE LOWER(referrer_email) = LOWER($1)",
            email,
        ) or 0
        completed = await conn.fetchval(
            """
            SELECT COUNT(*) FROM referrals
            WHERE LOWER(referrer_email) = LOWER($1) AND status = 'completed'
            """,
            email,
        ) or 0
    return {
        "invited":       int(invited),
        "completed":     int(completed),
        "months_earned": int(completed),   # 1 lună per referral completat
    }