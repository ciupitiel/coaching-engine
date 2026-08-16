"""
database_onboarding.py
Tracked trimitere emailuri de onboarding (ziua 1, 3, 7 după signup).

Tabel: onboarding_emails
  user_email TEXT
  day        INTEGER   (1, 3 sau 7)
  sent_at    TEXT
  UNIQUE(user_email, day) → imposibil de trimis de două ori același email
"""

import datetime
from database import get_pool


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

async def init_db_onboarding() -> None:
    """Creează tabelul onboarding_emails. Idempotent."""
    async with get_pool().acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS onboarding_emails (
                id         SERIAL  PRIMARY KEY,
                user_email TEXT    NOT NULL,
                day        INTEGER NOT NULL,
                sent_at    TEXT    NOT NULL,
                UNIQUE(user_email, day)
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_onboarding_email ON onboarding_emails(user_email)"
        )
    print("✅  onboarding_emails: OK")


# ─────────────────────────────────────────────────────────────────────────────
#  QUERIES
# ─────────────────────────────────────────────────────────────────────────────

async def get_users_for_onboarding_day(day: int) -> list[str]:
    """
    Returnează emailurile userilor care:
      • s-au înregistrat exact acum `day` zile (comparație pe dată, nu timestamp)
      • NU au primit deja emailul pentru ziua `day`

    Rulează zilnic din scheduler. Fereastra de ±0 zile e suficientă
    dacă scheduler-ul pornește consistent (APScheduler cu cron).
    """
    target_date = (
        datetime.date.today() - datetime.timedelta(days=day)
    ).isoformat()   # "YYYY-MM-DD"

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.email
            FROM   users u
            WHERE  LEFT(u.created_at, 10) = $1
              AND  u.email NOT IN (
                       SELECT oe.user_email
                       FROM   onboarding_emails oe
                       WHERE  oe.day = $2
                   )
            """,
            target_date,
            day,
        )
    return [row["email"] for row in rows]


async def mark_onboarding_sent(email: str, day: int) -> None:
    """
    Marchează emailul de onboarding `day` ca trimis pentru `email`.
    UNIQUE constraint ignoră duplicate (INSERT ... ON CONFLICT DO NOTHING).
    """
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO onboarding_emails (user_email, day, sent_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_email, day) DO NOTHING
            """,
            email.lower(),
            day,
            now,
        )