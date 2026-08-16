# =============================================================================
#  database_settings.py — Etapa 5: Settings Hub · Layer BD
#  Noian Cristian · Bazat pe inteligență artificială
#  -----------------------------------------------------------------------------
#  v1.1 FIX (două corecții în _DELETE_ORDER):
#    ① "calculation_sessions" → "sessions"  (tabelul real se numește "sessions")
#       anterior: ștergerea contului nu ștergea istoricul de calcule TDEE
#    ② Adăugat ("password_reset_tokens", "user_email")
#       anterior: token-urile de resetare parolă rămâneau în DB după ștergere cont
#  Restul codului: NEMODIFICAT față de versiunea anterioară.
# =============================================================================

import json
from datetime import datetime
from database import get_pool, get_profile, get_user_sessions, get_checkins

# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA — actualizată cu coloanele E5
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SETTINGS = """
CREATE TABLE IF NOT EXISTS user_settings (
    id                      SERIAL PRIMARY KEY,
    user_email              TEXT    NOT NULL UNIQUE,
    theme                   TEXT    NOT NULL DEFAULT 'dark',
    accent_color            TEXT    NOT NULL DEFAULT 'amber',
    density                 TEXT    NOT NULL DEFAULT 'comfortable',
    theme_sync              TEXT    NOT NULL DEFAULT 'manual',
    ai_persona              TEXT    NOT NULL DEFAULT 'empatic',
    diet_template           TEXT    NOT NULL DEFAULT 'standard',
    allergies               TEXT    NOT NULL DEFAULT '',
    adaptive_aggressiveness INTEGER NOT NULL DEFAULT 2,
    reduce_animations       BOOLEAN NOT NULL DEFAULT FALSE,
    units                   TEXT    NOT NULL DEFAULT 'metric',
    updated_at              TEXT    NOT NULL DEFAULT ''
)
"""

# ─────────────────────────────────────────────────────────────────────────────
#  VALORI DEFAULT
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS: dict = {
    "theme":                   "dark",
    "accent_color":            "amber",
    "density":                 "comfortable",
    "theme_sync":              "manual",
    "ai_persona":              "empatic",
    "diet_template":           "standard",
    "allergies":               "",
    "adaptive_aggressiveness": 2,
    "reduce_animations":       False,
    "units":                   "metric",
    "is_default":              True,
    "updated_at":              None,
}


# ─────────────────────────────────────────────────────────────────────────────
#  INIȚIALIZARE
# ─────────────────────────────────────────────────────────────────────────────

async def init_db_settings() -> None:
    """
    Creează tabelul user_settings dacă nu există. Idempotent.
    Apelat din lifespan() în main.py, după await init_db_p4().
    """
    async with get_pool().acquire() as conn:
        await conn.execute(_SCHEMA_SETTINGS)
    print("✅  user_settings: OK")


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def get_settings(email: str) -> dict:
    """
    Returnează settings-urile userului.
    Dacă nu a salvat niciodată → returnează DEFAULT_SETTINGS cu is_default=True.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM user_settings WHERE LOWER(user_email) = LOWER($1)",
            email,
        )

    if not row:
        return {**DEFAULT_SETTINGS}

    d = dict(row)
    d.pop("id", None)
    d.pop("user_email", None)
    d["is_default"] = False

    # Fallback pentru DB-uri care nu au primit încă migrarea E5
    d.setdefault("accent_color", "amber")
    d.setdefault("density",      "comfortable")
    d.setdefault("theme_sync",   "manual")

    return d


async def save_settings(email: str, settings: dict) -> dict:
    """
    Salvează (sau actualizează) settings-urile userului.
    UPSERT complet — un singur query indiferent că e prima salvare sau update.
    """
    # ── Validare câmpuri existente ────────────────────────────────────────────
    theme = settings.get("theme", "dark")
    if theme not in ("dark", "amoled"):
        theme = "dark"

    persona = settings.get("ai_persona", "empatic")
    if persona not in ("empatic", "stiintific", "militar"):
        persona = "empatic"

    diet = settings.get("diet_template", "standard")
    if diet not in ("standard", "keto", "mediteranean", "if_16_8"):
        diet = "standard"

    allergies = str(settings.get("allergies", ""))[:500]

    aggressiveness = int(settings.get("adaptive_aggressiveness", 2))
    if aggressiveness not in (1, 2, 3):
        aggressiveness = 2

    reduce_anim = bool(settings.get("reduce_animations", False))

    units = settings.get("units", "metric")
    if units not in ("metric", "hybrid"):
        units = "metric"

    # ── Validare câmpuri E5 ───────────────────────────────────────────────────
    accent_color = settings.get("accent_color", "amber")
    if accent_color not in ("amber", "cyan", "emerald", "violet", "white"):
        accent_color = "amber"

    density = settings.get("density", "comfortable")
    if density not in ("compact", "comfortable", "spacious"):
        density = "comfortable"

    theme_sync = settings.get("theme_sync", "manual")
    if theme_sync not in ("manual", "auto"):
        theme_sync = "manual"

    now = datetime.now().isoformat()

    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_settings
                (user_email, theme, accent_color, density, theme_sync,
                 ai_persona, diet_template, allergies,
                 adaptive_aggressiveness, reduce_animations, units, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (user_email) DO UPDATE SET
                theme                   = EXCLUDED.theme,
                accent_color            = EXCLUDED.accent_color,
                density                 = EXCLUDED.density,
                theme_sync              = EXCLUDED.theme_sync,
                ai_persona              = EXCLUDED.ai_persona,
                diet_template           = EXCLUDED.diet_template,
                allergies               = EXCLUDED.allergies,
                adaptive_aggressiveness = EXCLUDED.adaptive_aggressiveness,
                reduce_animations       = EXCLUDED.reduce_animations,
                units                   = EXCLUDED.units,
                updated_at              = EXCLUDED.updated_at
            """,
            email.lower(), theme, accent_color, density, theme_sync,
            persona, diet, allergies,
            aggressiveness, reduce_anim, units, now,
        )

    return {
        "theme":                   theme,
        "accent_color":            accent_color,
        "density":                 density,
        "theme_sync":              theme_sync,
        "ai_persona":              persona,
        "diet_template":           diet,
        "allergies":               allergies,
        "adaptive_aggressiveness": aggressiveness,
        "reduce_animations":       reduce_anim,
        "units":                   units,
        "updated_at":              now,
        "is_default":              False,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  EXPORT DATE
# ─────────────────────────────────────────────────────────────────────────────

async def get_all_user_data(email: str) -> dict:
    """
    Colectează TOATE datele unui user din toate tabelele.
    Folosit de /settings/export/json și /settings/export/csv.
    """
    from database_p4_additions import get_food_logs_range

    profile      = await get_profile(email)
    settings     = await get_settings(email)
    sessions     = await get_user_sessions(email, limit=500)
    checkins     = await get_checkins(email, limit=1000)

    today      = datetime.now().strftime("%Y-%m-%d")
    start_date = "2020-01-01"
    try:
        food_logs = await get_food_logs_range(email, start_date, today)
    except Exception:
        food_logs = []
    try:
        from database_exercise import get_exercise_logs_range as _get_ex
        exercise_logs = await _get_ex(email, start_date, today)
    except Exception:
        exercise_logs = []
    return {
        "export_date": datetime.now().isoformat(),
        "email":       email.lower(),
        "profile":     profile,
        "settings":    settings,
        "sessions":    sessions,
        "checkins":    checkins,
        "food_logs":   food_logs,
        "exercise_logs":  exercise_logs,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  ȘTERGERE CONT
# ─────────────────────────────────────────────────────────────────────────────

# FIX ①: "calculation_sessions" → "sessions" (tabelul real din database.py)
# FIX ②: adăugat "password_reset_tokens" (rămâneau în DB după ștergere cont)
# Ordinea: child tables first (nu avem FK constraints, dar e bună practică)
_DELETE_ORDER = [
    ("food_logs",             "user_email"),
    ("weight_checkins",       "user_email"),
    ("sessions",              "user_email"),   # FIX: era "calculation_sessions"
    ("user_profiles",         "user_email"),
    ("user_settings",         "user_email"),
    ("password_reset_tokens", "user_email"),   # NOU: curăță și token-urile de resetare
    ("email_verifications",   "user_email"),
    ("exercise_logs",         "user_email"),
    ("push_subscriptions",    "user_email"),
    ("users",                 "email"),
]


async def delete_user_data(email: str) -> dict:
    """
    Șterge TOATE datele unui user din toate tabelele cunoscute.
    Apelat DOAR din DELETE /settings/account cu confirm=True.
    """
    report: dict[str, int | str] = {}

    async with get_pool().acquire() as conn:
        for table, col in _DELETE_ORDER:
            try:
                result = await conn.execute(
                    f"DELETE FROM {table} WHERE LOWER({col}) = LOWER($1)",
                    email,
                )
                count = int(result.split()[-1])
                report[table] = count
            except Exception as exc:
                report[table] = f"skipped ({type(exc).__name__})"

    return report