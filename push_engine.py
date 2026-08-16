# =============================================================================
#  push_engine.py — Motor Push Notifications
#  Noian Cristian · Noian Lab
#  -----------------------------------------------------------------------------
#  v2.0: Adaugă job-ul de dimineață cu planuri AI personalizate.
#        Job-ul de seară (reminder fallback 20:00) rămâne neschimbat.
#
#  Scheduler timezone: Europe/Bucharest (DST automat, fără offset manual).
#  Necesită `tzdata` în requirements.txt (inclus de Python stdlib pe Linux;
#  explicit necesar pe macOS/Windows dev — adaugă tzdata în requirements.txt).
#
#  Două job-uri:
#    ① morning_plan_push   → 08:00 Romania  → AI meal plan + action buttons
#    ② daily_push_reminder → 20:00 Romania  → reminder fallback (fără log azi)
#    ③ daily_onboarding    → 09:00 UTC      → emailuri onboarding ziua 1/3/7
#
#  Concurență AI: max 5 apeluri Groq simultane (semaphore) — evită rate limiting.
# =============================================================================

import os
import json
import asyncio
import datetime
from zoneinfo import ZoneInfo

from groq import AsyncGroq
from pywebpush import webpush, WebPushException
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from email_service import send_onboarding_email
from database_onboarding import get_users_for_onboarding_day, mark_onboarding_sent
from database_push import (
    get_all_subscriptions,
    delete_push_subscription_by_endpoint,
    has_logged_today,
)
from database import get_pool
from database_morning_plan import save_morning_plan


# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURARE VAPID
#  FIX v1.1: VAPID_PRIVATE_KEY normalizat — .env stochează \n ca text literal.
# ─────────────────────────────────────────────────────────────────────────────

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY  = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_CLAIMS_SUB  = os.getenv("VAPID_CLAIMS_SUB", "mailto:contact@noianlab.ro")

_BUCHAREST_TZ = ZoneInfo("Europe/Bucharest")

# Singleton scheduler
_scheduler: AsyncIOScheduler | None = None


# ─────────────────────────────────────────────────────────────────────────────
#  GENERARE MESE AI — system prompt dedicat pentru plan zilnic
# ─────────────────────────────────────────────────────────────────────────────

_MORNING_SYSTEM_PROMPT = """Ești un nutriționist specializat în alimentația românească.
Generezi EXACT 3 mese pentru O SINGURĂ ZI, respectând targetul caloric primit.
Returnezi EXCLUSIV JSON VALID. Zero text suplimentar. Zero markdown. Doar JSON parsabil.

STRUCTURĂ JSON EXACTĂ — respectă cheile exact:
{
  "meals": [
    {
      "meal_label": "Mic Dejun",
      "meal_type": "mic_dejun",
      "name": "Omletă cu brânză și roșii",
      "description": "2 ouă bătute, 30g brânză telemea, 1 roșie, 5ml ulei",
      "calories": 320,
      "protein_g": 22,
      "carbs_g": 4,
      "fat_g": 24
    },
    {
      "meal_label": "Prânz",
      "meal_type": "pranz",
      "name": "Piept pui grătar cu orez",
      "description": "150g piept pui, 200g orez fiert, salată verde, lămâie",
      "calories": 510,
      "protein_g": 52,
      "carbs_g": 56,
      "fat_g": 7
    },
    {
      "meal_label": "Cină",
      "meal_type": "cina",
      "name": "Somon la cuptor cu cartofi dulci",
      "description": "150g file somon, 200g cartofi dulci la cuptor, lămâie, boia",
      "calories": 460,
      "protein_g": 36,
      "carbs_g": 38,
      "fat_g": 18
    }
  ]
}

REGULI ABSOLUTE:
· EXACT 3 mese în această ordine: Mic Dejun, Prânz, Cină
· meal_type EXACT: mic_dejun | pranz | cina (fără diacritice, snake_case)
· Suma calories din cele 3 mese ≈ target_kcal (±50 kcal toleranță)
· Proteina totală ≈ target_protein_g (±10g toleranță)
· Alimente 100% accesibile în România (supermarket local, piață)
· Porții ROMÂNEȘTI standard: piept pui=150g, orez fiert=200g, file pește=150g
· Varietate — nu același ingredient dominant la toate 3 mesele
· description: ingrediente cu gramaje exacte, scurt (max 60 caractere)
"""


async def _generate_morning_meals(
    target_kcal: int,
    protein_g:   int,
    carbs_g:     int,
    fat_g:       int,
    goal:        str,
) -> list[dict] | None:
    """
    Generează 3 mese pentru ziua curentă via Groq/Llama.
    Creează un client Groq nou (lightweight — fără state între sesiuni).
    Returnează lista de mese sau None la eroare.
    """
    groq_api_key = os.getenv("GROQ_API_KEY", "")
    if not groq_api_key:
        print("⚠️  _generate_morning_meals: GROQ_API_KEY lipsă în .env")
        return None

    client = AsyncGroq(api_key=groq_api_key)

    user_prompt = (
        f"Generează 3 mese pentru azi.\n"
        f"Target caloric: {target_kcal} kcal.\n"
        f"Macros: {protein_g}g proteină, {carbs_g}g carbohidrați, {fat_g}g grăsimi.\n"
        f"Obiectiv: {goal}."
    )

    try:
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _MORNING_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.35,   # Ușor creativ față de food_logger (0.15), consistent față de meal plan (0.55)
            max_tokens=700,
            top_p=0.9,
        )

        raw = response.choices[0].message.content.strip()

        # Strip markdown fences dacă modelul le adaugă
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]

        parsed = json.loads(raw.strip())
        meals  = parsed.get("meals", [])

        if len(meals) < 3:
            print(f"⚠️  _generate_morning_meals: mai puțin de 3 mese în răspuns")
            return None

        # Validare structură minimă
        required_keys = {"meal_label", "meal_type", "name", "calories", "protein_g", "carbs_g", "fat_g"}
        for m in meals:
            if not required_keys.issubset(m.keys()):
                print(f"⚠️  _generate_morning_meals: câmpuri lipsă în masă: {m}")
                return None

        return meals[:3]  # Strict 3 mese

    except json.JSONDecodeError as e:
        print(f"⚠️  _generate_morning_meals: JSON parse error: {e}")
        return None
    except Exception as exc:
        print(f"⚠️  _generate_morning_meals: {type(exc).__name__}: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def _get_user_last_session(email: str) -> dict | None:
    """Returnează target-ul caloric și macros din ultimul calcul TDEE al userului."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT target_kcal, protein_g, carbs_g, fat_g, goal
            FROM sessions
            WHERE LOWER(user_email) = LOWER($1)
              AND target_kcal IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            email,
        )
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
#  PAYLOAD BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_morning_payload(meals: list[dict], target_kcal: int, token: str) -> dict:
    """
    Construiește payload-ul push pentru planul de dimineață.

    Body format: "Ai 1.847 kcal azi · Omletă cu brânză · Piept pui cu orez · Somon la cuptor"
    Actions: "✓ Loghez tot" și "✗ Modific" — gestionate de service worker.
    requireInteraction: True → notificarea rămâne vizibilă până când userul interacționează.
    """
    meal_names = " · ".join(m.get("name", "?") for m in meals)
    body = f"Ai {target_kcal:,} kcal azi · {meal_names}"

    return {
        "title":              "Bună dimineața! ☀️",
        "body":               body,
        "tag":                f"morning-plan-{datetime.date.today().isoformat()}",
        "token":              token,
        "requireInteraction": True,   # Stă pe ecran până la acțiune (Android/Desktop)
        "actions": [
            {"action": "confirm-log", "title": "✓ Loghez tot"},
            {"action": "modify",      "title": "✗ Modific"},
        ],
        "url": "/?tab=nutritie",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  WEB PUSH — LOW LEVEL
# ─────────────────────────────────────────────────────────────────────────────

def _send_web_push_sync(
    endpoint: str,
    p256dh:   str,
    auth_key: str,
    payload:  dict,
) -> str:
    """
    Trimite un Web Push SINCRON (pywebpush nu e async).
    Apelat din asyncio.to_thread() pentru a nu bloca event loop-ul FastAPI.

    Returns: "ok" | "dead" (410/404 → de șters din DB) | "fail"
    """
    if not VAPID_PRIVATE_KEY:
        print(f"⚠️  VAPID_PRIVATE_KEY lipsă — push nesimulat pentru {endpoint[:40]}...")
        return "ok"

    try:
        webpush(
            subscription_info={
                "endpoint": endpoint,
                "keys": {"p256dh": p256dh, "auth": auth_key},
            },
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_CLAIMS_SUB},
            ttl=86400,  # 24h — re-livrare dacă browser offline
        )
        return "ok"

    except WebPushException as ex:
        status = 0
        if hasattr(ex, "response") and ex.response is not None:
            status = ex.response.status_code
        if status in (404, 410):
            return "dead"
        if status == 401:
            print("⚠️  VAPID auth error (401) — verifică VAPID_PRIVATE_KEY în .env")
            return "fail"
        print(f"⚠️  WebPushException {status}: {ex}")
        return "fail"

    except Exception as exc:
        print(f"⚠️  Push send error: {type(exc).__name__}: {exc}")
        return "fail"


async def send_push_notification(
    endpoint: str,
    p256dh:   str,
    auth_key: str,
    payload:  dict,
) -> bool:
    """
    Trimite un push notification async.
    Curăță automat endpoint-urile moarte din DB.
    """
    result = await asyncio.to_thread(
        _send_web_push_sync, endpoint, p256dh, auth_key, payload
    )
    if result == "dead":
        await delete_push_subscription_by_endpoint(endpoint)
        return False
    return result == "ok"


# ─────────────────────────────────────────────────────────────────────────────
#  JOB ① — MORNING PLAN (08:00 Romania)
# ─────────────────────────────────────────────────────────────────────────────

async def send_morning_plans() -> None:
    """
    Job la 08:00 Romania:
      Pentru fiecare user cu push subscription:
        1. Verifică dacă are sesiune TDEE (altfel skip — nu trimite generic)
        2. Generează 3 mese cu Groq/Llama bazat pe macros-urile lui
        3. Salvează planul în morning_plans cu token UUID
        4. Trimite push personalizat cu butoane "✓ Loghez tot" / "✗ Modific"

    Concurență: max 5 apeluri Groq simultane (Semaphore) — evită rate limiting.
    """
    today = datetime.date.today().isoformat()
    print(f"\n🌅  Morning Plans [{today}] · start")

    subscriptions = await get_all_subscriptions()
    if not subscriptions:
        print("ℹ️  Morning Plans: 0 subscriptions active\n")
        return

    sem = asyncio.Semaphore(5)
    sent = skipped = failed = 0

    async def _process_one(sub: dict) -> None:
        nonlocal sent, skipped, failed

        email    = sub["user_email"]
        endpoint = sub["endpoint"]
        p256dh   = sub["p256dh"]
        auth_key = sub["auth_key"]

        async with sem:
            # 1. Obținem targetul caloric din ultima sesiune
            session = await _get_user_last_session(email)
            if not session or not session.get("target_kcal"):
                skipped += 1
                return   # Fără date TDEE → fără plan personalizat

            target_kcal = int(session["target_kcal"])
            protein_g   = int(session.get("protein_g") or 0)
            carbs_g     = int(session.get("carbs_g")   or 0)
            fat_g       = int(session.get("fat_g")     or 0)
            goal        = session.get("goal") or "mentenanta"

            # 2. Generăm mese cu AI
            meals = await _generate_morning_meals(
                target_kcal, protein_g, carbs_g, fat_g, goal
            )
            if not meals:
                skipped += 1
                return

            # 3. Salvăm planul → obținem token
            try:
                token = await save_morning_plan(email, today, meals)
            except Exception as exc:
                print(f"⚠️  save_morning_plan({email}): {exc}")
                failed += 1
                return

            # 4. Trimitem push personalizat
            payload = _build_morning_payload(meals, target_kcal, token)
            ok = await send_push_notification(endpoint, p256dh, auth_key, payload)
            if ok:
                sent += 1
            else:
                failed += 1

    await asyncio.gather(*[_process_one(sub) for sub in subscriptions])
    print(
        f"✅  Morning Plans: {sent} trimise · {skipped} skip (fără TDEE/AI fail) "
        f"· {failed} eșuate / {len(subscriptions)} total\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  JOB ② — EVENING REMINDER (20:00 Romania) — fallback pentru userii fără log
# ─────────────────────────────────────────────────────────────────────────────

async def send_daily_reminders() -> None:
    """
    Job la 20:00 Romania:
    Trimite reminder generic DOAR utilizatorilor care nu au logat nicio masă azi.
    (Userii care au confirmat planul de dimineață au deja log → sunt skipiți automat.)
    """
    today = datetime.date.today().isoformat()
    print(f"\n🔔  Push Reminders [{today}] · start")

    subscriptions = await get_all_subscriptions()
    total = len(subscriptions)

    if not subscriptions:
        print(f"ℹ️  Push Reminders: 0 subscriptions active\n")
        return

    sent = skipped = failed = 0

    for sub in subscriptions:
        email    = sub["user_email"]
        endpoint = sub["endpoint"]
        p256dh   = sub["p256dh"]
        auth_key = sub["auth_key"]

        try:
            already_logged = await has_logged_today(email)
        except Exception as exc:
            print(f"⚠️  has_logged_today({email}): {exc}")
            skipped += 1
            continue

        if already_logged:
            skipped += 1
            continue

        weekday = datetime.date.today().weekday()
        bodies  = [
            "Nu ai logat nicio masă azi. Streak-ul tău te așteaptă!",
            "Loghează ce ai mâncat azi — 30 de secunde, rezultate reale.",
            "Ziua aproape s-a terminat. Log rapid înainte de miezul nopții!",
            "Consistența bate intensitatea. Loghează azi!",
        ]

        payload = {
            "title": "Noian Lab · Reminder",
            "body":  bodies[weekday % len(bodies)],
            "tag":   "daily-reminder",
            "url":   "/?tab=nutritie",
        }

        ok = await send_push_notification(endpoint, p256dh, auth_key, payload)
        if ok:
            sent += 1
        else:
            failed += 1

    print(
        f"✅  Push Reminders: {sent} trimise · {skipped} deja loggate "
        f"· {failed} eșuate / {total} total\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  JOB ③ — ONBOARDING EMAILS (intern, neschimbat)
# ─────────────────────────────────────────────────────────────────────────────

async def _run_onboarding_emails() -> None:
    for day in [1, 3, 7]:
        try:
            emails = await get_users_for_onboarding_day(day)
            for email in emails:
                ok = await send_onboarding_email(email, day)
                if ok:
                    await mark_onboarding_sent(email, day)
        except Exception as e:
            print(f"⚠️  Onboarding day {day} job error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEDULER
# ─────────────────────────────────────────────────────────────────────────────

def init_push_scheduler() -> None:
    """
    Inițializează APScheduler cu timezone Europe/Bucharest.
    DST (EEST/EET) gestionat automat — nu sunt necesare offset-uri manuale.

    Cele 3 job-uri:
      morning_plan_push   → 08:00 Romania  (plan AI personalizat)
      daily_push_reminder → 20:00 Romania  (reminder fallback)
      daily_onboarding    → 09:00 UTC      (emailuri onboarding)
    """
    global _scheduler

    _scheduler = AsyncIOScheduler(timezone=_BUCHAREST_TZ)

    _scheduler.add_job(
        func=send_morning_plans,
        trigger="cron",
        hour=8,
        minute=0,
        id="morning_plan_push",
        replace_existing=True,
        misfire_grace_time=600,   # ±10 minute toleranță (Render free tier poate dormi)
    )

    _scheduler.add_job(
        func=send_daily_reminders,
        trigger="cron",
        hour=20,
        minute=0,
        id="daily_push_reminder",
        replace_existing=True,
        misfire_grace_time=600,
    )

    _scheduler.add_job(
        func=_run_onboarding_emails,
        trigger="cron",
        hour=9,
        minute=0,
        timezone="UTC",           # Onboarding rămâne în UTC
        id="daily_onboarding",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    _scheduler.start()
    print("✅  Morning Push:  activ → 08:00 Romania (DST automat)")
    print("✅  Evening Push:  activ → 20:00 Romania (DST automat)")
    print("✅  Onboarding:    activ → 09:00 UTC")


def shutdown_push_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("Push Scheduler: oprit.")