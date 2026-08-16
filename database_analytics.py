import datetime
from database_p4_additions import get_food_logs_range

# ── Luni scurte în română ────────────────────────────────────────────────────
_MONTHS_RO = ['Ian', 'Feb', 'Mar', 'Apr', 'Mai', 'Iun',
               'Iul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# ── Zilele săptămânii în română (scurt, Luni=index 0) ───────────────────────
_WEEKDAYS_RO = ['Lu', 'Ma', 'Mi', 'Jo', 'Vi', 'Sâ', 'Du']

# ── Macros urmărite ──────────────────────────────────────────────────────────
_MACRO_KEYS = ('calories', 'protein_g', 'carbs_g', 'fat_g')
_ZERO       = dict.fromkeys(_MACRO_KEYS, 0)


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITAR: INTERVAL SĂPTĂMÂNAL
# ─────────────────────────────────────────────────────────────────────────────

def _week_range(week_offset: int = 0) -> tuple[datetime.date, datetime.date]:
    """
    Returnează (luni, duminică) pentru săptămâna offset-ată.

    week_offset=0  → săptămâna curentă (luni – duminică ISO)
    week_offset=-1 → săptămâna trecută
    week_offset=-2 → acum două săptămâni

    weekday() returnează 0=Luni … 6=Duminică (ISO).
    """
    today             = datetime.date.today()
    days_since_monday = today.weekday()                              # 0..6
    monday            = today - datetime.timedelta(
                            days=days_since_monday + (-week_offset * 7)
                        )
    sunday            = monday + datetime.timedelta(days=6)
    return monday, sunday


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITAR: GRUPARE LOGURI PE ZILE
# ─────────────────────────────────────────────────────────────────────────────

def _group_by_date(logs: list[dict]) -> dict[str, dict]:
    """
    Sumează macros per dată calendaristică.
    Returnează: { "YYYY-MM-DD": {calories, protein_g, carbs_g, fat_g} }
    """
    by_date: dict[str, dict] = {}
    for log in logs:
        d = log.get('date', '')
        if not d:
            continue
        if d not in by_date:
            by_date[d] = {**_ZERO}
        for k in _MACRO_KEYS:
            by_date[d][k] += int(log.get(k, 0))
    return by_date


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIA 1: SUMAR SĂPTĂMÂNAL
# ─────────────────────────────────────────────────────────────────────────────

async def get_weekly_food_summary(email: str, week_offset: int = 0) -> dict:
    """
    Agregă logurile alimentare dintr-o săptămână completă (Luni → Duminică).

    Args:
        email       : email-ul utilizatorului autentificat
        week_offset : 0=curentă, -1=trecută, -2=acum 2 săptămâni

    Returns:
        {
            week_start:      str  "YYYY-MM-DD" (luni)
            week_end:        str  "YYYY-MM-DD" (duminică)
            days_logged:     int  zile cu ≥1 log alimentar
            logs_count:      int  total înregistrări individuale
            avg_daily:       dict {calories, protein_g, carbs_g, fat_g}  ← medie pe zile loggate
            total:           dict {calories, protein_g, carbs_g, fat_g}  ← suma totală
            daily_breakdown: list 7 elemente (una per zi), include zile fără loguri
        }

    Câmpurile daily_breakdown:
        date:      "YYYY-MM-DD"
        weekday:   "Lu" | "Ma" | ... | "Du"
        has_logs:  bool — există loguri pentru ziua asta
        is_future: bool — ziua este în viitor (față de azi)
        calories, protein_g, carbs_g, fat_g: int (0 dacă no logs)
    """
    monday, sunday = _week_range(week_offset)
    today          = datetime.date.today()

    logs    = await get_food_logs_range(email, str(monday), str(sunday))
    by_date = _group_by_date(logs)

    days_logged = len(by_date)
    total       = {k: sum(d[k] for d in by_date.values()) for k in _MACRO_KEYS}
    avg_daily   = (
        {k: round(total[k] / days_logged) for k in _MACRO_KEYS}
        if days_logged > 0 else {**_ZERO}
    )

    # Breakdown complet: 7 zile inclusiv cele fără loguri și cele viitoare
    daily_breakdown = []
    for i in range(7):
        day     = monday + datetime.timedelta(days=i)
        day_str = str(day)
        day_data = by_date.get(day_str, {**_ZERO})
        daily_breakdown.append({
            'date':      day_str,
            'weekday':   _WEEKDAYS_RO[i],    # Lu, Ma, Mi, Jo, Vi, Sâ, Du
            'has_logs':  day_str in by_date,
            'is_future': day > today,
            **day_data,                       # calories, protein_g, carbs_g, fat_g
        })

    return {
        'week_start':      str(monday),
        'week_end':        str(sunday),
        'days_logged':     days_logged,
        'logs_count':      len(logs),
        'avg_daily':       avg_daily,
        'total':           total,
        'daily_breakdown': daily_breakdown,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIA 2: MEDII RULANTE (pentru grafic trend)
# ─────────────────────────────────────────────────────────────────────────────

async def get_rolling_weekly_averages(email: str, n_weeks: int = 4) -> list[dict]:
    """
    Returnează mediile zilnice ale ultimelor N săptămâni, cronologic (cel mai
    vechi primul). Include EXCLUSIV săptămânile cu cel puțin 1 zi logată.

    Args:
        email   : email-ul utilizatorului
        n_weeks : câte săptămâni să analizeze (default 4)

    Returns:
        [
            {
                week_start:  "YYYY-MM-DD"
                week_label:  "21 Iul"         ← pentru axa X din Chart.js
                days_logged: int
                calories:    int              ← medie zilnică
                protein_g:   int
                carbs_g:     int
                fat_g:       int
            },
            ...
        ]

    Exemplu offset-uri pentru n_weeks=4: [-3, -2, -1, 0]
    → acum 3 săpt., acum 2 săpt., săpt. trecută, săpt. curentă
    """
    results = []

    # range(-(n_weeks-1), 1) pentru n=4 → [-3, -2, -1, 0] (cronologic)
    for offset in range(-(n_weeks - 1), 1):
        summary = await get_weekly_food_summary(email, week_offset=offset)

        if summary['days_logged'] == 0:
            continue   # Ignorăm săptămânile fără niciun log

        # Label scurt pentru axa X: "21 Iul", "28 Iul", etc.
        ws    = datetime.date.fromisoformat(summary['week_start'])
        label = f"{ws.day} {_MONTHS_RO[ws.month - 1]}"

        results.append({
            'week_start':  summary['week_start'],
            'week_label':  label,
            'days_logged': summary['days_logged'],
            **summary['avg_daily'],   # calories, protein_g, carbs_g, fat_g
        })

    return results

# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIA 3: HEATMAP CALENDAR — compliance caloric zilnic
# ─────────────────────────────────────────────────────────────────────────────

async def get_heatmap_data(email: str, days: int = 84) -> list[dict]:
    """
    Returnează ultimele `days` zile cu calorii loggate per zi.
    days=84 → 12 săptămâni în urmă față de azi.

    Returns:
        [
            {
                'date':      'YYYY-MM-DD',
                'calories':  int | None,   # None = fără loguri
                'has_data':  bool,
                'is_future': bool,
            },
            ...
        ]
    Ordonat cronologic: cel mai vechi → cel mai recent.
    """
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days - 1)

    logs    = await get_food_logs_range(email, str(start), str(today))
    by_date = _group_by_date(logs)

    result = []
    for i in range(days):
        day     = start + datetime.timedelta(days=i)
        day_str = str(day)
        data    = by_date.get(day_str)
        result.append({
            'date':      day_str,
            'calories':  data['calories'] if data else None,
            'has_data':  data is not None,
            'is_future': day > today,
        })
    return result