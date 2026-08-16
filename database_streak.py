import datetime
from database import get_pool

_WEEKDAYS_RO = ['Lu', 'Ma', 'Mi', 'Jo', 'Vi', 'Sâ', 'Du']


async def compute_streak(email: str) -> dict:
    today     = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    cutoff    = today - datetime.timedelta(days=365)   

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT date
            FROM food_logs
            WHERE LOWER(user_email) = LOWER($1)
              AND date >= $2
            ORDER BY date DESC
            """,
            email, str(cutoff),
        )

    logged_dates = {row['date'] for row in rows}
    last_7       = _last_7_days(today, logged_dates)

    if not logged_dates:
        return {
            'current_streak': 0,
            'longest_streak': 0,
            'logged_today':   False,
            'is_alive':       False,
            'last_log_date':  None,
            'last_7_days':    last_7,
        }

    last_log_str  = max(logged_dates)
    last_log_date = datetime.date.fromisoformat(last_log_str)
    logged_today  = (last_log_date == today)
    is_alive      = (last_log_date >= yesterday)   

    current_streak = 0
    if is_alive:
        anchor = today if logged_today else yesterday
        day    = anchor
        while str(day) in logged_dates:
            current_streak += 1
            day -= datetime.timedelta(days=1)

    all_sorted     = sorted(datetime.date.fromisoformat(d) for d in logged_dates)
    longest_streak = _longest_run(all_sorted)

    return {
        'current_streak': current_streak,
        'longest_streak': longest_streak,
        'logged_today':   logged_today,
        'is_alive':       is_alive,
        'last_log_date':  last_log_str,
        'last_7_days':    last_7,
    }

def _longest_run(dates_sorted: list[datetime.date]) -> int:
    if not dates_sorted:
        return 0
    longest = current = 1
    for i in range(1, len(dates_sorted)):
        if (dates_sorted[i] - dates_sorted[i - 1]).days == 1:
            current += 1
            if current > longest:
                longest = current
        else:
            current = 1
    return longest


def _last_7_days(today: datetime.date, logged_dates: set) -> list[dict]:
    result = []
    for i in range(6, -1, -1):
        day = today - datetime.timedelta(days=i)
        result.append({
            'date':     str(day),
            'weekday':  _WEEKDAYS_RO[day.weekday()],
            'logged':   str(day) in logged_dates,
            'is_today': day == today,
        })
    return result