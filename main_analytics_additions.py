from fastapi import APIRouter, Depends
from auth import require_user_email
from premium_guard import require_premium
from database import get_user_sessions
from database_analytics import get_weekly_food_summary, get_rolling_weekly_averages, get_heatmap_data
from database_micronutrient import get_cached_spotlight, save_spotlight


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITAR: COMPLIANCE %
# ─────────────────────────────────────────────────────────────────────────────

def _compute_compliance(avg_daily: dict, targets: dict | None) -> dict | None:
    """
    Calculează procentul de atingere a fiecărui macro față de target.

    Returnat ca procente (0-150). Capat la 150% pentru a nu distorsiona
    vizualizarea în caz de supraconsum sever.

    Returns None dacă nu există targets (user fără calcul TDEE).
    """
    if not targets:
        return None

    def _pct(actual: int, target) -> int:
        if not target:
            return 0
        return min(150, round(actual / target * 100))

    return {
        'calories_pct': _pct(avg_daily.get('calories',  0), targets.get('calories')),
        'protein_pct':  _pct(avg_daily.get('protein_g', 0), targets.get('protein_g')),
        'carbs_pct':    _pct(avg_daily.get('carbs_g',   0), targets.get('carbs_g')),
        'fat_pct':      _pct(avg_daily.get('fat_g',     0), targets.get('fat_g')),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY ROUTER
# ─────────────────────────────────────────────────────────────────────────────

def init_analytics_router() -> APIRouter:
    """
    Creează și returnează router-ul E7 cu analytics săptămânale.
    Apelat O SINGURĂ DATĂ la pornirea serverului din main.py:
        app.include_router(init_analytics_router())
    """
    router = APIRouter(prefix='/analytics', tags=['E7 · Weekly Analytics'])

    # ── GET /analytics/weekly ─────────────────────────────────────────────────
    @router.get('/weekly')
    async def analytics_weekly(email: str = Depends(require_user_email)):
        """
        Returnează un snapshot complet al săptămânii curente vs. precedente.

        Structura răspunsului:
          current_week : sumar săptămâna curentă (din get_weekly_food_summary)
          prev_week    : sumar săptămâna trecută (pentru comparație delta)
          targets      : macro targets din ultimul calcul TDEE al userului
          compliance   : {calories_pct, protein_pct, carbs_pct, fat_pct} (0-150)
          delta        : {calories, protein_g, carbs_g, fat_g} avg_daily curentă − trecută
                         → None dacă una dintre săptămâni nu are date

        Exemplu delta: calories=-120 → mănâncă cu 120 kcal/zi mai puțin ca săptămâna trecută.
        """
        current_week, prev_week = await _fetch_both_weeks(email)
        targets                 = await _get_targets(email)
        compliance              = _compute_compliance(current_week['avg_daily'], targets)
        delta                   = _compute_delta(current_week, prev_week)

        return {
            'current_week': current_week,
            'prev_week':    prev_week,
            'targets':      targets,
            'compliance':   compliance,
            'delta':        delta,
        }

    # ── GET /analytics/trends ─────────────────────────────────────────────────
    @router.get('/trends')
    async def analytics_trends(email: str = Depends(require_premium)):
        """
        Returnează mediile zilnice ale ultimelor 4 săptămâni cu date loggate.
        Ordonat cronologic (cel mai vechi primul) — direct folosibil în Chart.js.

        Structura răspunsului:
          weeks           : [{week_start, week_label, days_logged, calories,
                               protein_g, carbs_g, fat_g}]
          target_kcal     : int | null   ← linia orizontală target pe grafic
          target_protein_g: int | null   ← linia target pe axa secundară

        Săptămânile cu 0 loguri sunt excluse complet din listă.
        """
        weeks   = await get_rolling_weekly_averages(email, n_weeks=4)
        targets = await _get_targets(email)

        return {
            'weeks':             weeks,
            'target_kcal':       (targets or {}).get('calories'),
            'target_protein_g':  (targets or {}).get('protein_g'),
        }

    # ── GET /analytics/heatmap ────────────────────────────────────────────────
    @router.get('/heatmap')
    async def analytics_heatmap(email: str = Depends(require_user_email)):
        """
        Calendar Heatmap — compliance caloric zilnic, ultimele 12 săptămâni.
        Disponibil pentru toți userii autentificați (free + premium).

        Response:
            days:        list[{date, calories, has_data, is_future}]
            target_kcal: int | null
        """
        days    = await get_heatmap_data(email, days=84)
        targets = await _get_targets(email)
        return {
            'days':        days,
            'target_kcal': (targets or {}).get('calories'),
        }

    # ── POST /analytics/micronutrient-spotlight ───────────────────────────────
    @router.post("/analytics/micronutrient-spotlight")
    async def micronutrient_spotlight(email: str = Depends(require_premium)):
        """
        Analizează alimentele loggate în ultimele 7 zile și semnalează
        riscuri probabile de deficit nutrițional.

        Premium-gated. Cache 7 zile (per săptămână ISO) — zero cost repetat.

        Response:
          cached    : bool
          week_of   : str
          nutrients : [{name, risk, reasoning, foods}]
          disclaimer: str
        """
        import os
        from groq import AsyncGroq
        from database_p4_additions import get_food_logs_range
        import datetime

        # ── 1. Verifică cache-ul ───────────────────────────────────────────
        cached = await get_cached_spotlight(email)
        if cached:
            cached["cached"] = True
            return cached

        # ── 2. Colectează logurile din ultima săptămână ────────────────────
        today = datetime.date.today()
        start = today - datetime.timedelta(days=6)
        logs  = await get_food_logs_range(email, str(start), str(today))

        if not logs:
            return {
                "cached":     False,
                "week_of":    f"{today.year}-W{today.isocalendar()[1]:02d}",
                "nutrients":  [],
                "disclaimer": "Nu există loguri alimentare în ultima săptămână.",
                "empty":      True,
            }

        # ── 3. Pregătește contextul pentru AI ─────────────────────────────
        food_names = []
        for log in logs:
            breakdown = log.get("breakdown_json") or []
            if isinstance(breakdown, str):
                import json as _json
                try:    breakdown = _json.loads(breakdown)
                except: breakdown = []
            for item in breakdown:
                if isinstance(item, dict) and item.get("name"):
                    food_names.append(item["name"])

        if not food_names:
            food_names = [log.get("description", "—") for log in logs[:10]]

        unique_foods = list(dict.fromkeys(food_names))[:60]
        foods_text   = ", ".join(unique_foods)

        # ── 4. Apel Groq ──────────────────────────────────────────────────
        client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY", ""))
        prompt = f"""Ești un nutriționist. Analizează aceste alimente consumate în ultima săptămână:

{foods_text}

Identifică 3-4 RISCURI PROBABILE de deficit nutrițional bazat pe tipurile de alimente prezente/absente.
Fii ONEST că sunt estimări, nu diagnoze. Răspunde STRICT în JSON, fără text în afara JSON-ului:

{{
  "nutrients": [
    {{
      "name": "Omega-3",
      "risk": "ridicat",
      "reasoning": "Lipsesc peștele gras, semințele de in, nucile din alimentație.",
      "foods": ["somon", "semințe de in", "nuci"]
    }}
  ],
  "disclaimer": "Acestea sunt estimări bazate pe tipare alimentare, nu diagnoze medicale."
}}

Valorile pentru 'risk': 'ridicat', 'moderat', 'scăzut'.
Nutrienți de verificat: Omega-3, Vitamina D, Magneziu, Fibre, Fier, Vitamina B12, Zinc, Calciu."""

        try:
            resp = await client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.25,
                max_tokens=600,
            )
            raw = resp.choices[0].message.content.strip()
            # Strip markdown dacă modelul îl adaugă
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            import json as _json
            result = _json.loads(raw.strip())
        except Exception as e:
            return {
                "cached":     False,
                "week_of":    f"{today.year}-W{today.isocalendar()[1]:02d}",
                "nutrients":  [],
                "disclaimer": f"Analiza temporar indisponibilă. Încearcă din nou.",
                "error":      True,
            }

        # ── 5. Salvează în cache și returnează ────────────────────────────
        week_key = f"{today.year}-W{today.isocalendar()[1]:02d}"
        result["cached"]  = False
        result["week_of"] = week_key
        await save_spotlight(email, result)
        return result

    return router


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITARE INTERNE
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_both_weeks(email: str) -> tuple[dict, dict]:
    """
    Fetch săptămâna curentă + precedentă.
    Sequential (nu paralel) — asyncpg pool-ul gestionează conexiunile,
    2 query-uri rapide nu justifică complexitatea asyncio.gather.
    """
    current = await get_weekly_food_summary(email, week_offset=0)
    prev    = await get_weekly_food_summary(email, week_offset=-1)
    return current, prev


async def _get_targets(email: str) -> dict | None:
    """
    Extrage macro targets din ultimul calcul TDEE al userului.
    Returnează None dacă userul nu a efectuat niciun calcul TDEE.
    """
    sessions     = await get_user_sessions(email, limit=1)
    last_session = sessions[0] if sessions else None

    if not last_session:
        return None

    return {
        'calories':  last_session.get('target_kcal'),
        'protein_g': last_session.get('protein_g'),
        'carbs_g':   last_session.get('carbs_g'),
        'fat_g':     last_session.get('fat_g'),
    }


def _compute_delta(current: dict, prev: dict) -> dict | None:
    """
    Diferența dintre avg_daily curentă și cea a săptămânii trecute.
    Returnează None dacă una din săptămâni nu are date (days_logged=0).

    Valori pozitive = mănâncă mai mult decât săptămâna trecută.
    Valori negative = mănâncă mai puțin.
    """
    if current['days_logged'] == 0 or prev['days_logged'] == 0:
        return None

    c, p = current['avg_daily'], prev['avg_daily']
    return {k: round(c[k] - p[k]) for k in ('calories', 'protein_g', 'carbs_g', 'fat_g')}