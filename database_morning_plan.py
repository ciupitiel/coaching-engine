# =============================================================================
#  database_morning_plan.py — Schema & CRUD pentru planul zilnic de dimineață
#  Noian Cristian · Noian Lab
#  -----------------------------------------------------------------------------
#  Adaugă în main.py exact 2 linii:
#
#  ① La importuri:
#       from database_morning_plan import init_db_morning_plan
#
#  ② În lifespan(), după `await init_db_push()`:
#       await init_db_morning_plan()
#
#  Tabel: morning_plans
#    user_email   TEXT  — proprietarul planului
#    plan_date    TEXT  — YYYY-MM-DD
#    plan_json    TEXT  — JSON: [{meal_label, meal_type, name, description,
#                                calories, protein_g, carbs_g, fat_g}]
#    token        TEXT  — UUID v4, unic, single-use (autentificarea SW-ului)
#    confirmed_at TEXT  — NULL = neconfirmat · ISO timestamp = logat
#
#  UNIQUE(user_email, plan_date): un singur plan per zi per user.
#  UPSERT la re-generare (misfire recovery): token-ul vechi devine invalid automat.
# =============================================================================

import datetime
import json
import uuid

from database import get_pool


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS morning_plans (
    id           SERIAL PRIMARY KEY,
    user_email   TEXT   NOT NULL,
    plan_date    TEXT   NOT NULL,
    plan_json    TEXT   NOT NULL,
    token        TEXT   NOT NULL UNIQUE,
    confirmed_at TEXT,
    created_at   TEXT   NOT NULL,
    UNIQUE(user_email, plan_date)
)
"""


async def init_db_morning_plan() -> None:
    """Creează tabelul morning_plans. Idempotent."""
    async with get_pool().acquire() as conn:
        await conn.execute(_SCHEMA)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_morning_token ON morning_plans(token)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_morning_user ON morning_plans(user_email)"
        )
    print("✅  morning_plans: OK")


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def save_morning_plan(email: str, plan_date: str, meals: list[dict]) -> str:
    """
    Salvează planul AI generat pentru dimineața zilei `plan_date`.
    Returnează token-ul UUID inclus în payload-ul push.

    UPSERT pe (user_email, plan_date): dacă job-ul rulează de două ori
    (misfire recovery Render free tier), planul și token-ul se rescriu —
    token-ul vechi devine automat invalid (UNIQUE pe token).
    """
    token = str(uuid.uuid4())
    now   = datetime.datetime.now().isoformat()

    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO morning_plans
                (user_email, plan_date, plan_json, token, confirmed_at, created_at)
            VALUES ($1, $2, $3, $4, NULL, $5)
            ON CONFLICT (user_email, plan_date) DO UPDATE SET
                plan_json    = EXCLUDED.plan_json,
                token        = EXCLUDED.token,
                confirmed_at = NULL,
                created_at   = EXCLUDED.created_at
            """,
            email.lower(),
            plan_date,
            json.dumps(meals, ensure_ascii=False),
            token,
            now,
        )

    return token


async def get_plan_by_token(token: str) -> dict | None:
    """
    Returnează planul asociat token-ului.
    None dacă token-ul nu există (invalid sau nu a fost generat încă).

    `already_confirmed` permite endpoint-ului să fie idempotent:
    service worker-ul poate retry fără să dubleze logurile.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT user_email, plan_date, plan_json, confirmed_at
            FROM morning_plans
            WHERE token = $1
            """,
            token,
        )

    if not row:
        return None

    return {
        "user_email":       row["user_email"],
        "plan_date":        row["plan_date"],
        "meals":            json.loads(row["plan_json"]),
        "already_confirmed": row["confirmed_at"] is not None,
    }


async def mark_plan_confirmed(token: str) -> None:
    """
    Marchează planul ca logat (single-use enforcement).
    Apelat imediat după bulk insert în food_logs.
    """
    now = datetime.datetime.now().isoformat()
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE morning_plans SET confirmed_at = $1 WHERE token = $2",
            now,
            token,
        )