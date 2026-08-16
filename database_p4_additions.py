import json
from datetime import datetime
from database import get_pool   # Reutilizăm pool-ul global — zero conexiuni extra


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA P4 — statements individuale (asyncpg nu suportă executescript)
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_P4_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS food_logs (
        id          SERIAL PRIMARY KEY,
        user_email  TEXT    NOT NULL,
        date        TEXT    NOT NULL,
        meal_type   TEXT    NOT NULL DEFAULT 'general',
        description TEXT    NOT NULL,
        calories    INTEGER NOT NULL DEFAULT 0,
        protein_g   INTEGER NOT NULL DEFAULT 0,
        carbs_g     INTEGER NOT NULL DEFAULT 0,
        fat_g       INTEGER NOT NULL DEFAULT 0,
        items_json  TEXT,
        confidence  TEXT    DEFAULT 'medium',
        notes       TEXT    DEFAULT '',
        created_at  TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_food_logs_email      ON food_logs(user_email)",
    "CREATE INDEX IF NOT EXISTS idx_food_logs_date       ON food_logs(date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_food_logs_email_date ON food_logs(user_email, date)",
]


async def init_db_p4() -> None:
    """
    Creează tabelul food_logs dacă nu există. Idempotent.
    Apelat din lifespan() în main.py, după await init_db().
    Reutilizează pool-ul deja inițializat din database.py.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        for stmt in _SCHEMA_P4_STATEMENTS:
            await conn.execute(stmt)
    print("✅  food_logs: OK")


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

async def save_food_log(
    email:       str,
    meal_type:   str,
    description: str,
    calories:    int,
    protein_g:   int,
    carbs_g:     int,
    fat_g:       int,
    items:       list,
    confidence:  str = "medium",
    notes:       str = "",
) -> dict:
    """
    Salvează un log alimentar nou.
    RETURNING id = înlocuiește cursor.lastrowid din versiunea SQLite.
    """
    now   = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO food_logs
                (user_email, date, meal_type, description, calories,
                 protein_g, carbs_g, fat_g, items_json, confidence, notes, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING id
            """,
            email.lower(), today, meal_type, description,
            int(calories), int(protein_g), int(carbs_g), int(fat_g),
            json.dumps(items, ensure_ascii=False),
            confidence, notes, now,
        )

    return {"id": row["id"], "date": today}


async def get_food_logs_by_date(email: str, date: str | None = None) -> list[dict]:
    """
    Returnează toate logurile dintr-o zi (default: azi), sortate cronologic ASC.
    items_json este parsat automat la dict — frontul primește obiecte, nu string JSON.
    """
    target = date or datetime.now().strftime("%Y-%m-%d")

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, date, meal_type, description, calories,
                   protein_g, carbs_g, fat_g, items_json,
                   confidence, notes, created_at
            FROM food_logs
            WHERE LOWER(user_email) = LOWER($1) AND date = $2
            ORDER BY created_at ASC
            """,
            email, target,
        )

    result = []
    for row in rows:
        d = dict(row)
        try:
            d["items"] = json.loads(d.pop("items_json") or "[]")
        except Exception:
            d["items"] = []
        result.append(d)
    return result


async def delete_food_log(email: str, log_id: int) -> bool:
    """
    Șterge un log specific care aparține userului autentificat.

    DELETE ... RETURNING id = mai curat decât a parsa string-ul "DELETE 1"
    returnat de asyncpg. Dacă row e None → logul nu exista sau nu aparține userului.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM food_logs
            WHERE id = $1 AND LOWER(user_email) = LOWER($2)
            RETURNING id
            """,
            log_id, email,
        )
    return row is not None


async def get_food_logs_range(
    email: str, start_date: str, end_date: str
) -> list[dict]:
    """
    Returnează logurile dintr-un interval de date (pentru rapoarte — P6/P7).
    start_date, end_date format: 'YYYY-MM-DD'
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, date, meal_type, description, calories,
                   protein_g, carbs_g, fat_g, confidence, notes
            FROM food_logs
            WHERE LOWER(user_email) = LOWER($1)
              AND date BETWEEN $2 AND $3
            ORDER BY date ASC, created_at ASC
            """,
            email, start_date, end_date,
        )
    return [dict(row) for row in rows]