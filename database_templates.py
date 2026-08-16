# =============================================================================
#  database_templates.py — Feature #18: Quick Meal Templates · DB Layer
#  Noian Cristian · Noian Lab
#  -----------------------------------------------------------------------------
#  Modul NOU. Nu modifică niciun fișier existent.
#
#  Tabel nou: meal_templates
#  Detectează mese repetitive și le oferă userului ca template-uri rapide.
#  Un tap = food_log salvat instant, zero AI, zero latență.
#
#  Interfață publică:
#    init_db_templates()
#    save_template(email, name, meal_type, description,
#                  calories, protein_g, carbs_g, fat_g, items, notes) → dict
#    get_templates(email) → list[dict]
#    increment_template_use(email, template_id) → dict | None
#    delete_template(email, template_id) → bool
#    get_description_frequency(email, description, days=60) → int
#    template_description_exists(email, description) → bool
# =============================================================================

import json
from datetime import datetime, timedelta
from database import get_pool

_MAX_TEMPLATES_PER_USER = 20   # limită anti-bloat: 20 template-uri per cont


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_TEMPLATES = [
    """
    CREATE TABLE IF NOT EXISTS meal_templates (
        id           SERIAL PRIMARY KEY,
        user_email   TEXT    NOT NULL,
        name         TEXT    NOT NULL,
        meal_type    TEXT    NOT NULL DEFAULT 'general',
        description  TEXT    NOT NULL,
        calories     INTEGER NOT NULL DEFAULT 0,
        protein_g    INTEGER NOT NULL DEFAULT 0,
        carbs_g      INTEGER NOT NULL DEFAULT 0,
        fat_g        INTEGER NOT NULL DEFAULT 0,
        items_json   TEXT,
        notes        TEXT    DEFAULT '',
        use_count    INTEGER NOT NULL DEFAULT 1,
        last_used_at TEXT    NOT NULL,
        created_at   TEXT    NOT NULL
    )
    """,

    # Index principal pe email — toate query-urile filtrează după user
    "CREATE INDEX IF NOT EXISTS idx_meal_templates_email ON meal_templates(user_email)",

    # Unicitate pe (user_email, descriere normalizată) — previne duplicate silențioase.
    # LOWER(TRIM(...)) → "2 Ouă cu Pâine " = "2 ouă cu pâine" = același template.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_meal_templates_user_desc
        ON meal_templates (user_email, LOWER(TRIM(description)))
    """,
]


# ─────────────────────────────────────────────────────────────────────────────
#  INIȚIALIZARE
# ─────────────────────────────────────────────────────────────────────────────

async def init_db_templates() -> None:
    """
    Creează tabelul meal_templates + indexele dacă nu există. Idempotent.
    Apelat din lifespan() în main.py, după await init_db_stripe().

        await init_db_templates()   # ← adaugă în lifespan, după init_db_stripe()
    """
    async with get_pool().acquire() as conn:
        for stmt in _SCHEMA_TEMPLATES:
            await conn.execute(stmt)
    print("✅  meal_templates: OK")


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def save_template(
    email:       str,
    name:        str,
    meal_type:   str,
    description: str,
    calories:    int,
    protein_g:   int,
    carbs_g:     int,
    fat_g:       int,
    items:       list,
    notes:       str = "",
) -> dict:
    """
    Salvează un template nou.

    Returnează dict cu datele template-ului inserat.

    Aruncă ValueError (HTTP 409 în endpoint) dacă:
      · Userul a atins limita de _MAX_TEMPLATES_PER_USER template-uri
      · Există deja un template cu aceeași descriere (case-insensitive, trimmed)

    Verificările se fac explicit la nivel aplicație înainte de INSERT pentru
    mesaje de eroare clare — UNIQUE INDEX-ul DB rămâne ultima linie de apărare.
    """
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    async with get_pool().acquire() as conn:

        # ── Verificare limită per user ─────────────────────────────────────
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM meal_templates WHERE LOWER(user_email) = LOWER($1)",
            email,
        )
        if count >= _MAX_TEMPLATES_PER_USER:
            raise ValueError(
                f"Ai atins limita de {_MAX_TEMPLATES_PER_USER} template-uri. "
                "Șterge unul pentru a adăuga altul."
            )

        # ── Verificare duplicat ────────────────────────────────────────────
        exists = await conn.fetchval(
            """
            SELECT id FROM meal_templates
            WHERE LOWER(user_email) = LOWER($1)
              AND LOWER(TRIM(description)) = LOWER(TRIM($2))
            """,
            email, description,
        )
        if exists:
            raise ValueError("Există deja un template salvat cu această descriere.")

        # ── INSERT ────────────────────────────────────────────────────────
        row = await conn.fetchrow(
            """
            INSERT INTO meal_templates
                (user_email, name, meal_type, description, calories,
                 protein_g, carbs_g, fat_g, items_json, notes,
                 use_count, last_used_at, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 1, $11, $11)
            RETURNING id, name, meal_type, description,
                      calories, protein_g, carbs_g, fat_g,
                      use_count, last_used_at
            """,
            email.lower(),
            name.strip()[:80],        # max 80 chars, silențios trunchiat
            meal_type,
            description,
            int(calories),
            int(protein_g),
            int(carbs_g),
            int(fat_g),
            json.dumps(items, ensure_ascii=False),
            notes,
            now,
        )

    return dict(row)


async def get_templates(email: str) -> list[dict]:
    """
    Returnează toate template-urile unui user.
    Ordine: use_count DESC, last_used_at DESC.
    Cele mai frecvent folosite apar primele — relevant pentru strip-ul din UI.

    items_json este parsat automat la list — frontul primește obiecte, nu string.
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, meal_type, description,
                   calories, protein_g, carbs_g, fat_g,
                   items_json, notes, use_count, last_used_at, created_at
            FROM meal_templates
            WHERE LOWER(user_email) = LOWER($1)
            ORDER BY use_count DESC, last_used_at DESC
            """,
            email,
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


async def increment_template_use(email: str, template_id: int) -> dict | None:
    """
    Atomically: incrementează use_count + actualizează last_used_at.
    Returnează datele complete ale template-ului (cu macros) sau None
    dacă template-ul nu există / nu aparține userului.

    Folosit exclusiv de POST /food/templates/{id}/use:
      · Fetch + update în o singură operație DB (fără race condition)
      · Returnează macros pentru a crea food_log-ul imediat, fără al doilea SELECT
    """
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE meal_templates
               SET use_count    = use_count + 1,
                   last_used_at = $1
             WHERE id = $2 AND LOWER(user_email) = LOWER($3)
            RETURNING id, name, meal_type, description,
                      calories, protein_g, carbs_g, fat_g,
                      items_json, notes, use_count
            """,
            now, template_id, email,
        )

    if not row:
        return None

    d = dict(row)
    try:
        d["items"] = json.loads(d.pop("items_json") or "[]")
    except Exception:
        d["items"] = []
    return d


async def delete_template(email: str, template_id: int) -> bool:
    """
    Șterge un template care aparține userului.
    Returnează True dacă a existat și a fost șters.
    Returnează False dacă nu aparținea userului (sau nu exista) — nu aruncă excepție.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM meal_templates
            WHERE id = $1 AND LOWER(user_email) = LOWER($2)
            RETURNING id
            """,
            template_id, email,
        )
    return row is not None


# ─────────────────────────────────────────────────────────────────────────────
#  DETECȚIE FRECVENȚĂ — sugestie automată de template
# ─────────────────────────────────────────────────────────────────────────────

async def get_description_frequency(
    email:       str,
    description: str,
    days:        int = 60,
) -> int:
    """
    Numără de câte ori userul a logat aceeași descriere (case-insensitive, trimmed)
    în ultimele `days` zile.

    Apelat din POST /food/log după salvare, pentru a detecta mese repetitive.
    Prag de sugestie: >= 3 apariții → afișăm banner de salvare template.

    Complexitate: O(n) pe food_logs al userului — eficient cu indexul existent
    idx_food_logs_email_date care acoperă și filtrarea pe date.
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    async with get_pool().acquire() as conn:
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM food_logs
            WHERE LOWER(user_email) = LOWER($1)
              AND date >= $2
              AND LOWER(TRIM(description)) = LOWER(TRIM($3))
            """,
            email, cutoff, description,
        )
    return int(count or 0)


async def template_description_exists(email: str, description: str) -> bool:
    """
    Verifică dacă există deja un template cu aceeași descriere.
    Previne afișarea sugestiei de salvare dacă templateul e deja creat.

    Apelat numai dacă get_description_frequency() >= 3, deci rar.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchval(
            """
            SELECT id FROM meal_templates
            WHERE LOWER(user_email) = LOWER($1)
              AND LOWER(TRIM(description)) = LOWER(TRIM($2))
            LIMIT 1
            """,
            email, description,
        )
    return row is not None