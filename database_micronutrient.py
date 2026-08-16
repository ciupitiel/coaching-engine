import datetime
import json
from database import get_pool


def _current_week_key() -> str:
    today = datetime.date.today()
    return f"{today.year}-W{today.isocalendar()[1]:02d}"


async def init_db_micronutrient() -> None:
    async with get_pool().acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS micronutrient_spotlights (
                id          SERIAL  PRIMARY KEY,
                user_email  TEXT    NOT NULL,
                week_of     TEXT    NOT NULL,
                result_json TEXT    NOT NULL,
                created_at  TEXT    NOT NULL,
                UNIQUE(user_email, week_of)
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mn_email ON micronutrient_spotlights(user_email)"
        )
    print("✅  micronutrient_spotlights: OK")


async def get_cached_spotlight(email: str) -> dict | None:
    week = _current_week_key()
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT result_json FROM micronutrient_spotlights
            WHERE LOWER(user_email) = LOWER($1) AND week_of = $2
            """,
            email, week,
        )
    return json.loads(row["result_json"]) if row else None


async def save_spotlight(email: str, result: dict) -> None:
    week = _current_week_key()
    now  = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO micronutrient_spotlights (user_email, week_of, result_json, created_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_email, week_of) DO UPDATE
                SET result_json = EXCLUDED.result_json,
                    created_at  = EXCLUDED.created_at
            """,
            email.lower(), week, json.dumps(result, ensure_ascii=False), now,
        )