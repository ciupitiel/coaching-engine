# =============================================================================
#  database_nutritionist.py — Nutritionist B2B Platform · DB Layer
#  Noian Lab
#  -----------------------------------------------------------------------------
#  Tabele noi (CREATE IF NOT EXISTS — idempotent, safe de rulat la fiecare start):
#
#    nutritionists          — conturi nutriționiști
#    nutritionist_clients   — relație nutriționist ↔ client
#
#  Toate operațiunile folosesc pool-ul existent din database.py (get_pool()).
# =============================================================================

import datetime
import secrets
import string

from database import get_pool   # pool-ul existent din proiect


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA — rulată la startup din init_nutritionist_db()
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS nutritionists (
        id                 SERIAL PRIMARY KEY,
        email              TEXT UNIQUE NOT NULL,
        name               TEXT NOT NULL DEFAULT '',
        business_name      TEXT DEFAULT '',
        invite_code        TEXT UNIQUE NOT NULL,
        is_active          BOOLEAN DEFAULT TRUE,
        plan_status        TEXT DEFAULT 'trial',
        trial_ends_at      TEXT,
        stripe_customer_id TEXT,
        created_at         TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_nutri_email       ON nutritionists(LOWER(email))",
    "CREATE INDEX IF NOT EXISTS idx_nutri_invite_code ON nutritionists(invite_code)",

    """
    CREATE TABLE IF NOT EXISTS nutritionist_clients (
        id                   SERIAL PRIMARY KEY,
        nutritionist_email   TEXT NOT NULL,
        client_email         TEXT NOT NULL,
        added_at             TEXT NOT NULL,
        UNIQUE(nutritionist_email, client_email)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_nc_nutri  ON nutritionist_clients(LOWER(nutritionist_email))",
    "CREATE INDEX IF NOT EXISTS idx_nc_client ON nutritionist_clients(LOWER(client_email))",
]


async def init_nutritionist_db() -> None:
    """Creează tabelele dacă nu există. Apelat din lifespan()."""
    async with get_pool().acquire() as conn:
        for stmt in _SCHEMA:
            await conn.execute(stmt)
    print("✅  Nutritionist schema: OK")


# ─────────────────────────────────────────────────────────────────────────────
#  INVITE CODE GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def _generate_invite_code(name: str) -> str:
    """
    Generează cod unic: NUT-{INITIALS}-{RANDOM4}
    Ex: NUT-NC-X7K2 pentru Noian Cristian
    """
    initials = "".join(w[0].upper() for w in name.split()[:2] if w) or "NL"
    rand = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
    return f"NUT-{initials}-{rand}"


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD NUTRIȚIONIȘTI
# ─────────────────────────────────────────────────────────────────────────────

async def create_nutritionist(
    email:         str,
    name:          str,
    business_name: str = "",
) -> dict:
    """
    Creează un cont de nutriționist cu 14 zile trial gratuit.
    Returnează dict cu datele contului.
    Ridică ValueError dacă email-ul există deja.
    """
    email = email.lower().strip()
    now   = datetime.datetime.now().isoformat()
    trial_ends = (
        datetime.datetime.now() + datetime.timedelta(days=14)
    ).isoformat()

    # Generăm cod unic (max 5 încercări pentru coliziuni extrem de rare)
    for _ in range(5):
        code = _generate_invite_code(name)
        async with get_pool().acquire() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM nutritionists WHERE invite_code = $1", code
            )
            if not existing:
                break

    async with get_pool().acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO nutritionists
                    (email, name, business_name, invite_code,
                     plan_status, trial_ends_at, created_at)
                VALUES ($1,$2,$3,$4,'trial',$5,$6)
                RETURNING *
                """,
                email, name.strip(), business_name.strip(), code, trial_ends, now,
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise ValueError("Un cont cu acest email există deja.")
            raise
    return dict(row)


async def get_nutritionist(email: str) -> dict | None:
    """Returnează contul nutriționistului sau None."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM nutritionists WHERE LOWER(email) = LOWER($1)", email
        )
    return dict(row) if row else None


async def get_nutritionist_by_invite_code(code: str) -> dict | None:
    """Lookup după codul de invitație."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM nutritionists WHERE invite_code = $1", code.upper().strip()
        )
    return dict(row) if row else None


async def is_nutritionist(email: str) -> bool:
    """Verifică rapid dacă email-ul aparține unui nutriționist."""
    async with get_pool().acquire() as conn:
        val = await conn.fetchval(
            "SELECT id FROM nutritionists WHERE LOWER(email) = LOWER($1) AND is_active = TRUE",
            email
        )
    return val is not None


async def update_nutritionist_profile(
    email: str,
    name: str | None = None,
    business_name: str | None = None,
) -> None:
    """Actualizează profilul nutriționistului."""
    updates, params, idx = [], [email], 2
    if name is not None:
        updates.append(f"name = ${idx}"); params.append(name.strip()); idx += 1
    if business_name is not None:
        updates.append(f"business_name = ${idx}"); params.append(business_name.strip()); idx += 1
    if not updates:
        return
    async with get_pool().acquire() as conn:
        await conn.execute(
            f"UPDATE nutritionists SET {', '.join(updates)} WHERE LOWER(email) = LOWER($1)",
            *params
        )


async def set_nutritionist_plan_status(
    email: str,
    status: str,
    stripe_customer_id: str | None = None,
) -> None:
    """Actualizează statusul planului (trial/active/cancelled)."""
    async with get_pool().acquire() as conn:
        if stripe_customer_id:
            await conn.execute(
                """UPDATE nutritionists
                   SET plan_status=$2, stripe_customer_id=$3
                   WHERE LOWER(email)=LOWER($1)""",
                email, status, stripe_customer_id
            )
        else:
            await conn.execute(
                "UPDATE nutritionists SET plan_status=$2 WHERE LOWER(email)=LOWER($1)",
                email, status
            )


# ─────────────────────────────────────────────────────────────────────────────
#  GESTIONARE CLIENȚI
# ─────────────────────────────────────────────────────────────────────────────

async def link_client_to_nutritionist(
    nutritionist_email: str,
    client_email:       str,
) -> bool:
    """
    Leagă un client la un nutriționist.
    Returnează True dacă s-a creat legătura, False dacă există deja.
    """
    now = datetime.datetime.now().isoformat()
    async with get_pool().acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO nutritionist_clients
                    (nutritionist_email, client_email, added_at)
                VALUES (LOWER($1), LOWER($2), $3)
                ON CONFLICT (nutritionist_email, client_email) DO NOTHING
                """,
                nutritionist_email, client_email, now
            )
            return True
        except Exception:
            return False


async def get_nutritionist_clients(nutritionist_email: str) -> list[str]:
    """Returnează lista de email-uri ale clienților unui nutriționist."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT client_email FROM nutritionist_clients
            WHERE LOWER(nutritionist_email) = LOWER($1)
            ORDER BY added_at DESC
            """,
            nutritionist_email
        )
    return [r["client_email"] for r in rows]


async def remove_client_from_nutritionist(
    nutritionist_email: str,
    client_email:       str,
) -> None:
    """Șterge relația nutriționist ↔ client."""
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            DELETE FROM nutritionist_clients
            WHERE LOWER(nutritionist_email)=LOWER($1)
              AND LOWER(client_email)=LOWER($2)
            """,
            nutritionist_email, client_email
        )


async def get_nutritionist_for_client(client_email: str) -> str | None:
    """Returnează email-ul nutriționistului asociat unui client, sau None."""
    async with get_pool().acquire() as conn:
        val = await conn.fetchval(
            """
            SELECT nutritionist_email FROM nutritionist_clients
            WHERE LOWER(client_email) = LOWER($1)
            LIMIT 1
            """,
            client_email
        )
    return val


async def get_all_nutritionists_summary() -> list[dict]:
    """Admin-only: returnează toți nutriționiștii cu numărul de clienți."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.*,
                   COUNT(nc.client_email) AS clients_count
            FROM nutritionists n
            LEFT JOIN nutritionist_clients nc
                   ON LOWER(nc.nutritionist_email) = LOWER(n.email)
            GROUP BY n.id
            ORDER BY n.created_at DESC
            """
        )
    return [dict(r) for r in rows]