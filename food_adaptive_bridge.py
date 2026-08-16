import datetime
from database import get_pool


async def get_food_intake_stats(email: str, days: int = 14) -> dict:
    """
    Calculează statistici de consum real din food_logs pentru ultimele N zile.

    Interogare unică GROUP BY date → sumă per zi → medie pe zilele loggate.
    Folosim media pe zilele CU loguri (nu pe toate zilele din perioadă) —
    o zi fără loguri nu înseamnă că userul n-a mâncat, ci că n-a logat.

    Args:
        email : emailul utilizatorului autentificat
        days  : numărul de zile analizate (default 14 → 2 săptămâni)

    Returns:
        {
            has_data:            bool   — are cel puțin 3 zile loggate
            days_with_logs:      int    — zile cu cel puțin un log
            days_analyzed:       int    — intervalul total (default 14)
            avg_daily_kcal:      int    — media zilnică reală (kcal)
            avg_daily_protein_g: int    — proteina medie zilnică (g)
            avg_daily_carbs_g:   int    — carbohidrați medii zilnici (g)
            avg_daily_fat_g:     int    — grăsimi medii zilnice (g)
            total_logs:          int    — numărul total de înregistrări
            logging_consistency: float — % zile loggate din total (0.0–1.0)
            date_from:           str   — "YYYY-MM-DD"
            date_to:             str   — "YYYY-MM-DD" (azi)
        }

    Prag minim: 3 zile loggate → has_data=True.
    Sub acest prag, media nu e reprezentativă și nu o injectăm în motor.
    """
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days - 1)  # inclusiv azi

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                date,
                SUM(calories)  AS day_kcal,
                SUM(protein_g) AS day_protein,
                SUM(carbs_g)   AS day_carbs,
                SUM(fat_g)     AS day_fat,
                COUNT(*)       AS log_count
            FROM food_logs
            WHERE LOWER(user_email) = LOWER($1)
              AND date >= $2
            GROUP BY date
            ORDER BY date ASC
            """,
            email, str(start),
        )

    # Prag minim: cel puțin 3 zile loggate pentru o medie reprezentativă
    MIN_DAYS_FOR_STATS = 3

    if not rows or len(rows) < MIN_DAYS_FOR_STATS:
        return {
            "has_data":            False,
            "days_with_logs":      len(rows) if rows else 0,
            "days_analyzed":       days,
            "avg_daily_kcal":      0,
            "avg_daily_protein_g": 0,
            "avg_daily_carbs_g":   0,
            "avg_daily_fat_g":     0,
            "total_logs":          sum(r["log_count"] for r in rows) if rows else 0,
            "logging_consistency": round(len(rows) / days, 2) if rows else 0.0,
            "date_from":           str(start),
            "date_to":             str(today),
            "insufficient_reason": (
                f"Ai logat {len(rows) if rows else 0} din {days} zile. "
                f"Bridge-ul pornește la {MIN_DAYS_FOR_STATS} zile loggate."
            ),
        }

    days_with_logs = len(rows)
    total_logs     = sum(r["log_count"] for r in rows)

    # Media pe zilele CU loguri — corectă din punct de vedere nutrițional
    avg_kcal    = round(sum(r["day_kcal"]    for r in rows) / days_with_logs)
    avg_protein = round(sum(r["day_protein"] for r in rows) / days_with_logs)
    avg_carbs   = round(sum(r["day_carbs"]   for r in rows) / days_with_logs)
    avg_fat     = round(sum(r["day_fat"]     for r in rows) / days_with_logs)

    # Consistența loggingului: câte % din zile au date
    consistency = round(days_with_logs / days, 2)

    return {
        "has_data":            True,
        "days_with_logs":      days_with_logs,
        "days_analyzed":       days,
        "avg_daily_kcal":      avg_kcal,
        "avg_daily_protein_g": avg_protein,
        "avg_daily_carbs_g":   avg_carbs,
        "avg_daily_fat_g":     avg_fat,
        "total_logs":          total_logs,
        "logging_consistency": consistency,
        "date_from":           str(start),
        "date_to":             str(today),
    }


def build_food_context_for_ai(
    food_stats:   dict,
    target_kcal:  int | None,
) -> str:
    """
    Construiește blocul de context alimentar pentru prompt-ul AI din _bg_adaptive_analysis.

    Args:
        food_stats  : output-ul din get_food_intake_stats()
        target_kcal : targetul caloric din ultimul calcul TDEE

    Returns:
        String formatat, gata de injectat în system prompt AI.
    """
    if not food_stats.get("has_data"):
        days_logged = food_stats.get("days_with_logs", 0)
        days_total  = food_stats.get("days_analyzed", 14)
        return (
            f"CONSUM REAL: Fără date suficiente "
            f"({days_logged}/{days_total} zile loggate în ultimele {days_total} zile). "
            f"Analiza se bazează exclusiv pe datele de greutate."
        )

    avg_k   = food_stats["avg_daily_kcal"]
    avg_p   = food_stats["avg_daily_protein_g"]
    avg_c   = food_stats["avg_daily_carbs_g"]
    avg_f   = food_stats["avg_daily_fat_g"]
    logged  = food_stats["days_with_logs"]
    total   = food_stats["days_analyzed"]
    consist = round(food_stats["logging_consistency"] * 100)

    lines = [
        f"CONSUM REAL (ultimele {total} zile · {logged} zile loggate · {consist}% consistență):",
        f"  Medie zilnică: {avg_k} kcal · Proteină: {avg_p}g · Carbohidrați: {avg_c}g · Grăsimi: {avg_f}g.",
    ]

    # Comparație cu targetul — cea mai valoroasă informație pentru AI
    if target_kcal and target_kcal > 0:
        diff     = avg_k - target_kcal
        diff_pct = round(abs(diff) / target_kcal * 100)
        comply   = round(avg_k / target_kcal * 100)

        if diff < -100:
            lines.append(
                f"  ⚠ DEFICIT DE ADERENȚĂ: mănânci cu {abs(diff)} kcal/zi MAI PUȚIN decât targetul "
                f"({comply}% compliance). Aceasta poate explica o rată de schimbare diferită de plan."
            )
        elif diff > 100:
            lines.append(
                f"  ⚠ SURPLUS DE ADERENȚĂ: mănânci cu {diff} kcal/zi MAI MULT decât targetul "
                f"({comply}% compliance). Verifică porțiile și estimările AI."
            )
        else:
            lines.append(
                f"  ✓ Aderență excelentă: {comply}% din target "
                f"(diferență de doar {abs(diff)} kcal/zi față de plan)."
            )

    return "\n".join(lines)