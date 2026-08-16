# =============================================================================
#  database_programs.py — Structured Programs · DB Layer
#  Noian Lab
#  -----------------------------------------------------------------------------
#  Tabele:
#    programs       — catalog programe disponibile (admin-managed)
#    user_programs  — achizițiile userilor
#
#  Adaugă în main.py lifespan:
#    from database_programs import init_db_programs
#    await init_db_programs()
# =============================================================================

import datetime
import secrets
from database import get_pool


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS programs (
        id               SERIAL PRIMARY KEY,
        slug             TEXT UNIQUE NOT NULL,
        name             TEXT NOT NULL,
        tagline          TEXT DEFAULT '',
        description      TEXT DEFAULT '',
        duration_label   TEXT DEFAULT '',
        category         TEXT DEFAULT 'nutritie',
        price_eur        NUMERIC(8,2) NOT NULL DEFAULT 29.99,
        stripe_price_id  TEXT DEFAULT '',
        content_url      TEXT DEFAULT '',
        features         TEXT DEFAULT '[]',
        is_active        BOOLEAN DEFAULT TRUE,
        created_at       TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_prog_slug   ON programs(slug)",
    "CREATE INDEX IF NOT EXISTS idx_prog_active ON programs(is_active)",
    """
    CREATE TABLE IF NOT EXISTS user_programs (
        id                 SERIAL PRIMARY KEY,
        user_email         TEXT NOT NULL,
        program_id         INTEGER NOT NULL REFERENCES programs(id),
        stripe_session_id  TEXT DEFAULT '',
        purchased_at       TEXT NOT NULL,
        UNIQUE(user_email, program_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_up_email ON user_programs(LOWER(user_email))",
]


async def init_db_programs() -> None:
    async with get_pool().acquire() as conn:
        for stmt in _SCHEMA:
            await conn.execute(stmt)
        await _seed_default_programs(conn)
    print("✅  Programs schema: OK")


# ─────────────────────────────────────────────────────────────────────────────
#  SEED — programe default (inserate o singură dată, idempotent)
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_PROGRAMS = [
    {
        "slug":           "slabire-30-zile",
        "name":           "Slăbire 5kg în 30 de Zile",
        "tagline":        "Plan structurat, zero guess-work",
        "description":    "Program complet de 30 de zile cu plan alimentar zilnic, macros calculate personalizat și check-in-uri săptămânale. Potrivit pentru oricine vrea să slăbească 4–6 kg fără să numere calorii manual.",
        "duration_label": "30 zile",
        "category":       "nutritie",
        "price_eur":      29.99,
        "features":       '["Plan alimentar 30 zile", "Macros personalizate", "4 check-in-uri săptămânale", "Suport chat 24/7", "Acces 6 luni"]',
    },
    {
        "slug":           "masa-musculara-8-saptamani",
        "name":           "Masă Musculară · 8 Săptămâni",
        "tagline":        "Bulk curat, fără grăsime în plus",
        "description":    "Program de 8 săptămâni optimizat pentru câștig muscular maxim cu acumulare minimă de grăsime. Include plan alimentar progresiv, ajustări săptămânale de calorii și ghid de antrenament.",
        "duration_label": "8 săptămâni",
        "category":       "nutritie",
        "price_eur":      49.99,
        "features":       '["Plan alimentar 56 zile", "Calorii progressive", "Ghid antrenament inclus", "Ajustări săptămânale", "Acces 12 luni"]',
    },
    {
        "slug":           "recompozitie-12-saptamani",
        "name":           "Recompoziție Corporală · 12 Săptămâni",
        "tagline":        "Scade grăsimea, crește masa",
        "description":    "Cel mai avansat program — recompoziție simultană: slăbești grăsime și construiești mușchi în același timp. Necesită consistență și minim 3 antrenamente/săptămână.",
        "duration_label": "12 săptămâni",
        "category":       "nutritie",
        "price_eur":      69.99,
        "features":       '["Plan alimentar 84 zile", "Ciclare calorii", "Faze de deficit și surplus", "Suport prioritar", "Acces pe viață"]',
    },
]


async def _seed_default_programs(conn) -> None:
    now = datetime.datetime.now().isoformat()
    for p in _DEFAULT_PROGRAMS:
        exists = await conn.fetchval(
            "SELECT id FROM programs WHERE slug = $1", p["slug"]
        )
        if not exists:
            await conn.execute(
                """
                INSERT INTO programs
                    (slug, name, tagline, description, duration_label,
                     category, price_eur, features, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                p["slug"], p["name"], p["tagline"], p["description"],
                p["duration_label"], p["category"],
                float(p["price_eur"]), p["features"], now,
            )


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def get_all_programs() -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM programs WHERE is_active=TRUE ORDER BY price_eur ASC"
        )
    return [dict(r) for r in rows]


async def get_program_by_slug(slug: str) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM programs WHERE slug=$1 AND is_active=TRUE", slug
        )
    return dict(row) if row else None


async def get_program_by_id(program_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM programs WHERE id=$1", program_id)
    return dict(row) if row else None


async def get_user_programs(email: str) -> list[dict]:
    """Returnează programele achiziționate de user cu detalii."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.*, up.purchased_at, up.stripe_session_id
            FROM user_programs up
            JOIN programs p ON p.id = up.program_id
            WHERE LOWER(up.user_email) = LOWER($1)
            ORDER BY up.purchased_at DESC
            """,
            email,
        )
    return [dict(r) for r in rows]


async def has_user_program(email: str, program_id: int) -> bool:
    async with get_pool().acquire() as conn:
        val = await conn.fetchval(
            """
            SELECT id FROM user_programs
            WHERE LOWER(user_email)=LOWER($1) AND program_id=$2
            """,
            email, program_id,
        )
    return val is not None


async def grant_user_program(
    email: str,
    program_id: int,
    stripe_session_id: str = "",
) -> dict:
    """Acordă acces la program după plată confirmată."""
    now = datetime.datetime.now().isoformat()
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO user_programs
                (user_email, program_id, stripe_session_id, purchased_at)
            VALUES (LOWER($1), $2, $3, $4)
            ON CONFLICT (user_email, program_id) DO UPDATE
                SET stripe_session_id = EXCLUDED.stripe_session_id
            RETURNING *
            """,
            email, program_id, stripe_session_id, now,
        )
    return dict(row)


async def update_program_stripe_price(program_id: int, stripe_price_id: str) -> None:
    """Admin: setează Price ID-ul Stripe după crearea produsului în Dashboard."""
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE programs SET stripe_price_id=$2 WHERE id=$1",
            program_id, stripe_price_id,
        )


async def update_program_content_url(program_id: int, content_url: str) -> None:
    """Admin: setează URL-ul conținutului (Google Drive, Notion, etc.)."""
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE programs SET content_url=$2 WHERE id=$1",
            program_id, content_url,
        )