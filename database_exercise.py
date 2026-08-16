# =============================================================================
#  database_exercise.py — #16: Exerciții & Calorii Arse · DB Layer
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Modul NOU. Nu modifică niciun fișier existent.
#  Creează tabelul exercise_logs — zero impact pe tabele existente.
#
#  Integrare în main.py (4 linii — detalii în main_exercise_additions.py):
#    ① Import: from database_exercise import init_db_exercise
#    ② lifespan(): await init_db_exercise()  ← după await init_db_push()
#    ③ Import: from main_exercise_additions import init_exercise_router
#    ④ Router: app.include_router(init_exercise_router(groq_client))
#
#  Funcții publice:
#    init_db_exercise()                  → idempotent, din lifespan()
#    save_exercise_log(...)              → salvează antrenament nou
#    get_exercise_logs_by_date(...)      → loguri dintr-o zi (default azi)
#    delete_exercise_log(...)            → șterge un log specific
#    get_exercise_logs_range(...)        → interval de date (rapoarte)
#    get_exercise_summary_today(...)     → total kcal arse + minute azi
# =============================================================================

import datetime
from database import get_pool


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_EXERCISE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS exercise_logs (
        id              SERIAL PRIMARY KEY,
        user_email      TEXT    NOT NULL,
        date            TEXT    NOT NULL,
        description     TEXT    NOT NULL,
        exercise_name   TEXT    NOT NULL,
        exercise_type   TEXT    NOT NULL DEFAULT 'general',
        duration_min    INTEGER NOT NULL DEFAULT 0,
        calories_burned INTEGER NOT NULL DEFAULT 0,
        met_value       REAL    NOT NULL DEFAULT 0.0,
        intensity       TEXT    NOT NULL DEFAULT 'moderat',
        notes           TEXT    DEFAULT '',
        created_at      TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_exercise_email ON exercise_logs(user_email)",
    "CREATE INDEX IF NOT EXISTS idx_exercise_date  ON exercise_logs(date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_exercise_email_date ON exercise_logs(user_email, date)",
]


# ─────────────────────────────────────────────────────────────────────────────
#  INIȚIALIZARE
# ─────────────────────────────────────────────────────────────────────────────

async def init_db_exercise() -> None:
    """
    Creează tabelul exercise_logs dacă nu există. Idempotent.
    Apelat din lifespan() în main.py, după await init_db_push().
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        for stmt in _SCHEMA_EXERCISE_STATEMENTS:
            await conn.execute(stmt)
    print("✅  exercise_logs: OK")


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def save_exercise_log(
    email:           str,
    description:     str,
    exercise_name:   str,
    exercise_type:   str,
    duration_min:    int,
    calories_burned: int,
    met_value:       float,
    intensity:       str,
    notes:           str = "",
) -> dict:
    """
    Salvează un log de antrenament nou.

    RETURNING id = înglobăm id-ul nou creat fără SELECT suplimentar.
    Returnează {id, date} pentru update imediat în UI.
    """
    now   = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO exercise_logs
                (user_email, date, description, exercise_name, exercise_type,
                 duration_min, calories_burned, met_value, intensity, notes, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id
            """,
            email.lower(), today, description, exercise_name, exercise_type,
            int(duration_min), int(calories_burned), float(met_value),
            intensity, notes, now,
        )

    return {"id": row["id"], "date": today}


async def get_exercise_logs_by_date(email: str, date: str | None = None) -> list[dict]:
    """
    Returnează toate logurile de antrenament dintr-o zi (default: azi),
    sortate cronologic ASC (cel mai vechi primul — pentru UI list).
    """
    target = date or datetime.datetime.now().strftime("%Y-%m-%d")

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, date, description, exercise_name, exercise_type,
                   duration_min, calories_burned, met_value, intensity, notes, created_at
            FROM exercise_logs
            WHERE LOWER(user_email) = LOWER($1) AND date = $2
            ORDER BY created_at ASC
            """,
            email, target,
        )

    return [dict(row) for row in rows]


async def delete_exercise_log(email: str, log_id: int) -> bool:
    """
    Șterge un log specific care aparține userului autentificat.

    DELETE ... RETURNING id → dacă row e None, logul nu exista
    sau nu aparținea userului — nu expunem diferența (securitate).
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM exercise_logs
            WHERE id = $1 AND LOWER(user_email) = LOWER($2)
            RETURNING id
            """,
            log_id, email,
        )
    return row is not None


async def get_exercise_logs_range(
    email: str, start_date: str, end_date: str
) -> list[dict]:
    """
    Returnează logurile dintr-un interval de date (pentru rapoarte PDF, analytics).
    start_date, end_date format: 'YYYY-MM-DD'
    Sortat ASC → compatibil cu Chart.js fără re-sortare în frontend.
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, date, exercise_name, exercise_type,
                   duration_min, calories_burned, met_value, intensity, notes
            FROM exercise_logs
            WHERE LOWER(user_email) = LOWER($1)
              AND date BETWEEN $2 AND $3
            ORDER BY date ASC, created_at ASC
            """,
            email, start_date, end_date,
        )
    return [dict(row) for row in rows]


async def get_exercise_summary_today(email: str) -> dict:
    """
    Totalul caloriilor arse și minutelor de mișcare azi.

    Un singur query GROUP agregat — zero overhead.
    Folosit în header-ul zilnic pentru 'X kcal arse, Y minute'.
    COALESCE(SUM(...), 0): returnează 0 nu None când nu există loguri.
    """
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*)                          AS total_logs,
                COALESCE(SUM(calories_burned), 0) AS total_burned,
                COALESCE(SUM(duration_min), 0)    AS total_minutes
            FROM exercise_logs
            WHERE LOWER(user_email) = LOWER($1) AND date = $2
            """,
            email, today,
        )

    return {
        "date":            today,
        "total_logs":      int(row["total_logs"]    or 0),
        "calories_burned": int(row["total_burned"]  or 0),
        "total_minutes":   int(row["total_minutes"] or 0),
    }