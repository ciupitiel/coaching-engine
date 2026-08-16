# =============================================================================
#  database.py — PostgreSQL async (asyncpg) — Etapa 3
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Migrat din SQLite (aiosqlite) → PostgreSQL (asyncpg)
#  Compatibil cu Neon.tech și Supabase (tier gratuit)
#
#  Diferențe față de versiunea SQLite:
#    · aiosqlite.connect(DB_PATH)  →  pool.acquire()
#    · Placeholder ?               →  $1, $2, $3 ...
#    · aiosqlite.Row               →  asyncpg.Record (dict(row) identic)
#    · AUTOINCREMENT               →  SERIAL
#    · executescript               →  execute() statements individuale
#    · sqlite3.IntegrityError      →  asyncpg.UniqueViolationError
#    · cursor.lastrowid            →  RETURNING id
#    · SELECT + UPDATE/INSERT      →  ON CONFLICT ... DO UPDATE (UPSERT nativ)
#
#  Interfață publică NEMODIFICATĂ față de v1 SQLite:
#    init_pool(), get_pool(), init_db()
#    create_user(), get_user_by_email()
#    get_user_sessions(), save_session()
#    save_profile(), get_profile()
#    save_checkin(), get_checkins(), get_checkin_summary()
#    get_all_clients(), get_client_history(), get_global_stats()
# =============================================================================

import asyncpg
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()   # Citește .env din folderul curent — OBLIGATORIU înainte de os.environ.get

# DATABASE_URL din .env
# Neon:     postgresql://user:pass@ep-xxx.us-east-1.aws.neon.tech/dbname?sslmode=require
# Supabase: postgresql://postgres:pass@db.xxx.supabase.co:5432/postgres?sslmode=require
# Local:    postgresql://coaching:coaching@localhost:5432/coaching_db
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Pool global — inițializat O SINGURĂ DATĂ în lifespan() din main.py
_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    """
    Creează pool-ul de conexiuni PostgreSQL.
    Apelat PRIMUL în lifespan(), înainte de init_db().

    min_size=2  : 2 conexiuni mereu deschise (fără latență la primele request-uri)
    max_size=10 : Neon/Supabase free tier suportă 10-20 conexiuni simultane
    """
    global _pool
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL lipsește din .env! "
            "Adaugă URL-ul de la Neon.tech sau Supabase."
        )
    _pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    preview = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL[:40]
    print(f"✅  PostgreSQL pool: OK → {preview}")


def get_pool() -> asyncpg.Pool:
    """
    Returnează pool-ul existent.
    Sincron intenționat — apelat din funcțiile async via get_pool().acquire().
    """
    if _pool is None:
        raise RuntimeError(
            "Pool DB neinițializat. Verifică ordinea în lifespan(): "
            "await init_pool() trebuie apelat înaintea await init_db()."
        )
    return _pool


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA — PostgreSQL
#  Fiecare statement separat (asyncpg nu suportă executescript multi-statement).
#  SERIAL = echivalentul INTEGER PRIMARY KEY AUTOINCREMENT din SQLite.
#  UNIQUE(user_email, date) pe weight_checkins permite UPSERT nativ.
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_STATEMENTS = [
    # ── sessions — tabelul original de calcule ─────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id               SERIAL PRIMARY KEY,
        timestamp        TEXT    NOT NULL,
        client_name      TEXT    NOT NULL,
        age              INTEGER,
        sex              TEXT,
        weight_kg        REAL,
        height_cm        REAL,
        activity_level   TEXT,
        goal             TEXT,
        body_type        TEXT,
        formula_used     TEXT    DEFAULT 'Mifflin-St Jeor',
        estimated_bf     REAL,
        lbm_kg           REAL,
        bmr              INTEGER,
        tdee             INTEGER,
        target_kcal      INTEGER,
        protein_g        INTEGER,
        carbs_g          INTEGER,
        fat_g            INTEGER,
        total_kcal       INTEGER,
        weekly_rate      REAL,
        weekly_display   TEXT,
        coaching_insight TEXT,
        user_email       TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_client ON sessions(LOWER(client_name))",
    "CREATE INDEX IF NOT EXISTS idx_time   ON sessions(timestamp DESC)",

    # ── users — conturi clienți ────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS users (
        id            SERIAL PRIMARY KEY,
        email         TEXT    UNIQUE NOT NULL,
        password_hash TEXT    NOT NULL,
        created_at    TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",

    # ── user_profiles — profil persistent ─────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS user_profiles (
        id                SERIAL PRIMARY KEY,
        user_email        TEXT    UNIQUE NOT NULL,
        height_cm         REAL,
        age               INTEGER,
        sex               TEXT,
        activity_level    TEXT,
        goal              TEXT,
        initial_weight_kg REAL,
        updated_at        TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_profiles_email ON user_profiles(user_email)",

    # ── weight_checkins — istoricul de greutate ────────────────────────────
    # UNIQUE(user_email, date) permite ON CONFLICT UPSERT (un singur check-in/zi)
    """
    CREATE TABLE IF NOT EXISTS weight_checkins (
        id          SERIAL PRIMARY KEY,
        user_email  TEXT    NOT NULL,
        date        TEXT    NOT NULL,
        weight_kg   REAL    NOT NULL,
        notes       TEXT    DEFAULT '',
        created_at  TEXT    NOT NULL,
        UNIQUE(user_email, date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_checkins_email ON weight_checkins(user_email)",
    "CREATE INDEX IF NOT EXISTS idx_checkins_date  ON weight_checkins(date DESC)",
]


async def init_db() -> None:
    """
    Creează toate tabelele dacă nu există. Idempotent.
    Apelat din lifespan(), DUPĂ init_pool().
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        for stmt in _SCHEMA_STATEMENTS:
            await conn.execute(stmt)
    print("✅  Schema PostgreSQL: OK")


# ─────────────────────────────────────────────────────────────────────────────
#  CONTURI & AUTENTIFICARE
# ─────────────────────────────────────────────────────────────────────────────

async def create_user(email: str, password_hash: str) -> bool:
    """
    Creează un cont nou.
    Returnează True dacă a reușit, False dacă emailul există deja.
    asyncpg.UniqueViolationError = echivalentul sqlite3.IntegrityError.
    """
    try:
        async with get_pool().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ($1, $2, $3)
                """,
                email.lower(),
                password_hash,
                datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            )
        return True
    except asyncpg.UniqueViolationError:
        return False


async def get_user_by_email(email: str) -> dict | None:
    """Caută un utilizator după email pentru verificare la Login."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE email = $1",
            email.lower(),
        )
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
#  SESIUNI DE CALCUL
# ─────────────────────────────────────────────────────────────────────────────

async def get_user_sessions(email: str, limit: int = 50) -> list[dict]:
    """Returnează istoricul de calcule al unui cont, cel mai recent primul."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, timestamp, client_name, age, sex, weight_kg,
                   height_cm, activity_level, goal, formula_used,
                   estimated_bf, lbm_kg, bmr, tdee, target_kcal,
                   protein_g, carbs_g, fat_g, coaching_insight
            FROM sessions
            WHERE LOWER(user_email) = LOWER($1)
            ORDER BY timestamp DESC
            LIMIT $2
            """,
            email, limit,
        )
    return [dict(row) for row in rows]


async def save_session(
    profile, calc: dict, insight: str, user_email: str = None
) -> None:
    """
    Salvează o sesiune completă de calcul.
    Signatura IDENTICĂ cu versiunea SQLite — main.py nu necesită modificări.
    """
    m  = calc.get("macros", {})
    wc = calc.get("weekly_change", {})

    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (
                timestamp, client_name, age, sex, weight_kg, height_cm,
                activity_level, goal, body_type, formula_used,
                estimated_bf, lbm_kg,
                bmr, tdee, target_kcal,
                protein_g, carbs_g, fat_g, total_kcal,
                weekly_rate, weekly_display, coaching_insight,
                user_email
            ) VALUES (
                $1,  $2,  $3,  $4,  $5,  $6,
                $7,  $8,  $9,  $10,
                $11, $12,
                $13, $14, $15,
                $16, $17, $18, $19,
                $20, $21, $22,
                $23
            )
            """,
            datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            profile.name, profile.age, profile.sex,
            profile.weight_kg, profile.height_cm,
            profile.activity_level, profile.goal,
            getattr(profile, "body_type", None),
            calc.get("formula_used", "Mifflin-St Jeor"),
            calc.get("effective_bf_pct"), calc.get("lbm_kg"),
            calc.get("bmr"), calc.get("tdee"), calc.get("target_calories"),
            m.get("protein_g"), m.get("carbs_g"), m.get("fat_g"), m.get("total_kcal"),
            wc.get("rate"), wc.get("display"), insight,
            user_email,
        )


# ─────────────────────────────────────────────────────────────────────────────
#  PROFIL PERSISTENT
# ─────────────────────────────────────────────────────────────────────────────

async def save_profile(
    email: str, height_cm: float, age: int, sex: str,
    activity_level: str, goal: str, initial_weight_kg: float,
) -> None:
    """
    Upsert profil utilizator.
    ON CONFLICT(user_email) DO UPDATE = echivalentul INSERT OR REPLACE din SQLite.
    """
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_profiles
                (user_email, height_cm, age, sex, activity_level, goal,
                 initial_weight_kg, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (user_email) DO UPDATE SET
                height_cm         = EXCLUDED.height_cm,
                age               = EXCLUDED.age,
                sex               = EXCLUDED.sex,
                activity_level    = EXCLUDED.activity_level,
                goal              = EXCLUDED.goal,
                initial_weight_kg = EXCLUDED.initial_weight_kg,
                updated_at        = EXCLUDED.updated_at
            """,
            email.lower(), height_cm, age, sex,
            activity_level, goal, initial_weight_kg,
            datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        )


async def get_profile(email: str) -> dict | None:
    """Returnează profilul persistent. None dacă nu există încă."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM user_profiles WHERE LOWER(user_email) = LOWER($1)",
            email,
        )
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
#  CHECK-IN-URI DE GREUTATE
# ─────────────────────────────────────────────────────────────────────────────

async def save_checkin(
    email: str, weight_kg: float, notes: str = ""
) -> dict:
    """
    Salvează greutatea de azi. Un singur check-in pe zi (UPSERT).

    UNIQUE(user_email, date) din schemă + ON CONFLICT elimină necesitatea
    SELECT-ului manual de verificare din versiunea SQLite.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    now   = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    async with get_pool().acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM weight_checkins WHERE user_email = $1 AND date = $2",
            email.lower(), today,
        )
        await conn.execute(
            """
            INSERT INTO weight_checkins (user_email, date, weight_kg, notes, created_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_email, date) DO UPDATE SET
                weight_kg  = EXCLUDED.weight_kg,
                notes      = EXCLUDED.notes,
                created_at = EXCLUDED.created_at
            """,
            email.lower(), today, weight_kg, notes, now,
        )

    return {
        "date":      today,
        "weight_kg": weight_kg,
        "notes":     notes,
        "updated":   bool(existing),
    }


async def get_checkins(email: str, limit: int = 90) -> list[dict]:
    """Returnează istoricul de check-in-uri sortat ASC (perfect pentru Chart.js)."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT date, weight_kg, notes
            FROM weight_checkins
            WHERE LOWER(user_email) = LOWER($1)
            ORDER BY date ASC
            LIMIT $2
            """,
            email, limit,
        )
    return [dict(row) for row in rows]


async def get_checkin_summary(email: str) -> dict:
    """
    Statistici rapide: total, min, max, prima și ultima greutate.
    Folosit de AI prompt și de dashboard header.

    Notă: asyncpg nu permite reutilizarea unui parametru ($1 de mai multe ori),
    de aceea pasăm email de 3 ori ca $1, $2, $3.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*)       AS total,
                MIN(weight_kg) AS min_kg,
                MAX(weight_kg) AS max_kg,
                (SELECT weight_kg FROM weight_checkins
                 WHERE LOWER(user_email) = LOWER($1)
                 ORDER BY date ASC  LIMIT 1) AS first_kg,
                (SELECT weight_kg FROM weight_checkins
                 WHERE LOWER(user_email) = LOWER($2)
                 ORDER BY date DESC LIMIT 1) AS last_kg
            FROM weight_checkins
            WHERE LOWER(user_email) = LOWER($3)
            """,
            email, email, email,
        )

    if not row or row["total"] == 0:
        return {"total": 0}
    return dict(row)


# ─────────────────────────────────────────────────────────────────────────────
#  QUERIES GLOBALE (nemodificate ca logică, adaptate la asyncpg)
# ─────────────────────────────────────────────────────────────────────────────

async def get_all_clients() -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                client_name,
                COUNT(*)        AS sessions,
                MIN(timestamp)  AS first_seen,
                MAX(timestamp)  AS last_seen,
                (SELECT weight_kg FROM sessions s2
                 WHERE LOWER(s2.client_name) = LOWER(s.client_name)
                 ORDER BY timestamp DESC LIMIT 1) AS latest_weight,
                (SELECT weight_kg FROM sessions s3
                 WHERE LOWER(s3.client_name) = LOWER(s.client_name)
                 ORDER BY timestamp ASC  LIMIT 1) AS initial_weight,
                (SELECT goal FROM sessions s4
                 WHERE LOWER(s4.client_name) = LOWER(s.client_name)
                 ORDER BY timestamp DESC LIMIT 1) AS latest_goal,
                (SELECT target_kcal FROM sessions s5
                 WHERE LOWER(s5.client_name) = LOWER(s.client_name)
                 ORDER BY timestamp DESC LIMIT 1) AS latest_target
            FROM sessions s
            GROUP BY LOWER(client_name), client_name
            ORDER BY last_seen DESC
            """
        )
    return [dict(r) for r in rows]


async def get_client_history(client_name: str, limit: int = 30) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM sessions
            WHERE LOWER(client_name) = LOWER($1)
            ORDER BY timestamp DESC
            LIMIT $2
            """,
            client_name, limit,
        )
    return [dict(r) for r in rows]


async def get_global_stats() -> dict:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(DISTINCT LOWER(client_name)) AS total_clients,
                COUNT(*)                            AS total_sessions,
                ROUND(AVG(target_kcal))             AS avg_target
            FROM sessions
            """
        )
    return dict(row) if row else {"total_clients": 0, "total_sessions": 0, "avg_target": 0}