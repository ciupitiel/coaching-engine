# =============================================================================
#  database_settings_e5.py — Etapa 5: Schema Migration (additivă)
#  Noian Cristian · Bazat pe inteligență artificială
#  -----------------------------------------------------------------------------
#  Modul NOU. Adaugă în main.py exact 2 linii:
#
#  ① La importuri (lângă `from database_settings import ...`):
#     from database_settings_e5 import init_db_settings_e5
#
#  ② În lifespan(), IMEDIAT DUPĂ `await init_db_settings()`:
#     await init_db_settings_e5()
#
#  Total modificări în main.py: 2 linii. Zero înlocuiri.
#  -----------------------------------------------------------------------------
#  Ce face:
#    Adaugă 3 coloane noi la tabelul existent user_settings:
#      · accent_color  — culoarea de accent aleasă de user (preset key)
#      · density       — densitatea interfeței (compact/comfortable/spacious)
#      · theme_sync    — sincronizare cu OS (manual/auto)
#
#  De ce ALTER TABLE și nu recreare:
#    · Tabelul există deja în producție cu date reale.
#    · `ADD COLUMN IF NOT EXISTS` este idempotent în PostgreSQL 9.6+.
#    · Valorile DEFAULT se aplică automat la rândurile existente.
#    · Zero downtime, zero pierdere de date.
#
#  Coloanele noi sunt preluate automat de get_settings() și save_settings()
#  din database_settings.py (versiunea actualizată E5).
# =============================================================================

from database import get_pool

# ─────────────────────────────────────────────────────────────────────────────
#  MIGRATION STATEMENTS — ordonate și idempotente
# ─────────────────────────────────────────────────────────────────────────────

_E5_MIGRATION_STATEMENTS = [
    # accent_color: cheia presetului ales (amber/cyan/emerald/violet/white)
    # 'amber' = culoarea originală (#c4622d) — zero impact vizual la upgrade
    """
    ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS accent_color TEXT NOT NULL DEFAULT 'amber'
    """,

    # density: controlul densității vizuale
    # 'comfortable' = layout-ul actual — zero schimbare vizuală la upgrade
    """
    ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS density TEXT NOT NULL DEFAULT 'comfortable'
    """,

    # theme_sync: comportamentul față de preferința SO
    # 'manual' = user controlează manual — comportament existent
    """
    ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS theme_sync TEXT NOT NULL DEFAULT 'manual'
    """,
]


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIE DE MIGRARE — apelată din lifespan()
# ─────────────────────────────────────────────────────────────────────────────

async def init_db_settings_e5() -> None:
    """
    Adaugă coloanele Etapa 5 la tabelul user_settings dacă nu există.
    Complet idempotent — sigur de rulat la fiecare startup.

    Apelat din lifespan() în main.py, DUPĂ await init_db_settings():
        await init_db_settings_e5()

    Nu recreează tabelul. Nu șterge date. Nu blochează DB-ul.
    ALTER TABLE pe PostgreSQL este instant pentru ADD COLUMN cu DEFAULT literal.
    """
    async with get_pool().acquire() as conn:
        for stmt in _E5_MIGRATION_STATEMENTS:
            await conn.execute(stmt)

    print("✅  user_settings E5: accent_color, density, theme_sync — migrare OK")