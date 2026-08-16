"""
database_coach_v2.py
Recomandări coach → client și linking coach-client.

Tabele:
  coach_recommendations — mesaje trimise de coach unui client specific
  coach_clients         — relația coach ↔ client (client se leagă de coach prin cod)
"""

import datetime
from database import get_pool


async def init_db_coach_v2() -> None:
    async with get_pool().acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS coach_recommendations (
                id           SERIAL  PRIMARY KEY,
                coach_email  TEXT    NOT NULL,
                client_email TEXT    NOT NULL,
                message      TEXT    NOT NULL,
                created_at   TEXT    NOT NULL,
                read_at      TEXT
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_coachrec_client ON coach_recommendations(client_email)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_coachrec_coach  ON coach_recommendations(coach_email)"
        )
    print("✅  coach_recommendations: OK")


async def send_recommendation(
    coach_email: str, client_email: str, message: str
) -> int:
    """Salvează o recomandare. Returnează ID-ul nou."""
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO coach_recommendations (coach_email, client_email, message, created_at)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            coach_email.lower(), client_email.lower(), message.strip(), now,
        )
    return row["id"]


async def get_recommendations_for_client(client_email: str, limit: int = 10) -> list[dict]:
    """Returnează ultimele recomandări primite de un client."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, coach_email, message, created_at, read_at
            FROM coach_recommendations
            WHERE LOWER(client_email) = LOWER($1)
            ORDER BY created_at DESC
            LIMIT $2
            """,
            client_email, limit,
        )
    return [dict(r) for r in rows]


async def get_recommendations_sent_by_coach(
    coach_email: str, client_email: str, limit: int = 20
) -> list[dict]:
    """Returnează recomandările trimise de coach unui client specific."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, message, created_at, read_at
            FROM coach_recommendations
            WHERE LOWER(coach_email) = LOWER($1)
              AND LOWER(client_email) = LOWER($2)
            ORDER BY created_at DESC
            LIMIT $3
            """,
            coach_email, client_email, limit,
        )
    return [dict(r) for r in rows]


async def mark_recommendations_read(client_email: str) -> None:
    """Marchează toate recomandările necitite ca citite."""
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE coach_recommendations
            SET read_at = $1
            WHERE LOWER(client_email) = LOWER($2) AND read_at IS NULL
            """,
            now, client_email,
        )


async def count_unread_recommendations(client_email: str) -> int:
    """Numărul de recomandări necitite pentru un client."""
    async with get_pool().acquire() as conn:
        n = await conn.fetchval(
            """
            SELECT COUNT(*) FROM coach_recommendations
            WHERE LOWER(client_email) = LOWER($1) AND read_at IS NULL
            """,
            client_email,
        )
    return int(n or 0)