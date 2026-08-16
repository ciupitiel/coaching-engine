import datetime
from database import get_pool


_SCHEMA_PUSH = """
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          SERIAL PRIMARY KEY,
    user_email  TEXT    NOT NULL,
    endpoint    TEXT    NOT NULL UNIQUE,
    p256dh      TEXT    NOT NULL,
    auth_key    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
)
"""


# ─────────────────────────────────────────────────────────────────────────────
#  INIȚIALIZARE
# ─────────────────────────────────────────────────────────────────────────────

async def init_db_push() -> None:
    """
    Creează tabelul push_subscriptions dacă nu există. Idempotent.
    Apelat din lifespan() în main.py, după init_db_email_verification().
    """
    async with get_pool().acquire() as conn:
        await conn.execute(_SCHEMA_PUSH)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_push_email ON push_subscriptions(user_email)"
        )
    print("✅  push_subscriptions: OK")


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def save_push_subscription(
    email:    str,
    endpoint: str,
    p256dh:   str,
    auth_key: str,
) -> dict:
    """
    Salvează (sau actualizează) un push subscription.
    UPSERT pe endpoint — acelaşi browser poate re-subscribe cu endpoint schimbat;
    ON CONFLICT actualizează cheia și emailul (în caz de re-login).
    """
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()

    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO push_subscriptions (user_email, endpoint, p256dh, auth_key, created_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (endpoint) DO UPDATE SET
                user_email = EXCLUDED.user_email,
                p256dh     = EXCLUDED.p256dh,
                auth_key   = EXCLUDED.auth_key,
                created_at = EXCLUDED.created_at
            """,
            email.lower(), endpoint, p256dh, auth_key, now,
        )
    return {"ok": True}


async def delete_push_subscription(email: str, endpoint: str) -> bool:
    """
    Șterge un subscription specific al unui user (la unsubscribe din UI).
    Verifică emailul pentru securitate — nu poți șterge subscriptions ale altcuiva.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM push_subscriptions
            WHERE LOWER(user_email) = LOWER($1) AND endpoint = $2
            RETURNING id
            """,
            email, endpoint,
        )
    return row is not None


async def delete_push_subscription_by_endpoint(endpoint: str) -> None:
    """
    Șterge un endpoint mort (status 410 Gone de la browser).
    Apelat intern din push_engine.py la trimitere — curăță automat subscriptions invalide.
    Nu necesită autentificare (endpoint-ul mort e oricum inutilizabil).
    """
    async with get_pool().acquire() as conn:
        await conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = $1",
            endpoint,
        )


async def get_all_subscriptions() -> list[dict]:
    """
    Returnează TOATE subscriptions active din baza de date.
    Apelat de send_daily_reminders() din push_engine.py la 20:00.
    Fără paginare — presupunem < 10.000 useri în faza actuală.
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_email, endpoint, p256dh, auth_key FROM push_subscriptions"
        )
    return [dict(row) for row in rows]


async def get_user_subscriptions(email: str) -> list[dict]:
    """
    Returnează subscriptions-urile unui user specific.
    Folosit de GET /push/status pentru a afișa statusul în Settings.
    Un user poate avea max 2-3 subscriptions (desktop, mobil, tablet).
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT endpoint, p256dh, auth_key, created_at
            FROM push_subscriptions
            WHERE LOWER(user_email) = LOWER($1)
            ORDER BY created_at DESC
            """,
            email,
        )
    return [dict(row) for row in rows]


async def has_logged_today(email: str) -> bool:
    """
    Verifică dacă userul a logat cel puțin o masă azi.
    Folosit de send_daily_reminders() pentru a filtra userii care nu au nevoie de reminder.

    Query direct pe food_logs — nu importăm database_p4_additions pentru a evita
    dependențe circulare (database_push → database → pool, fără deps suplimentare).
    """
    today = str(datetime.date.today())
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 FROM food_logs
            WHERE LOWER(user_email) = LOWER($1) AND date = $2
            LIMIT 1
            """,
            email, today,
        )
    return row is not None