from datetime import datetime

KCAL_PER_KG = 7700   # 1 kg masă corporală ≈ 7700 kcal (standard clinic)

# Rata de schimbare săptămânală targetată per obiectiv (kg/săpt.)
GOAL_TARGET_RATES: dict[str, float] = {
    "cut_bland":  -0.20,
    "cut":        -0.40,
    "mentinere":   0.00,
    "bulk_lean":  +0.20,
    "bulk":       +0.40,
}

MIN_CHECKINS = 2
MIN_DAYS     = 7


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITARE
# ─────────────────────────────────────────────────────────────────────────────

def _days_between(d1: str, d2: str) -> int:
    fmt = "%Y-%m-%d"
    return abs((datetime.strptime(d2, fmt) - datetime.strptime(d1, fmt)).days)


def _compute_confidence(n_checkins: int, n_days: int) -> str:
    """
    Nivelul de încredere al estimării TDEE real.
    High = 10+ check-in-uri pe 30+ zile (date foarte solide)
    Medium = 5+ check-in-uri pe 14+ zile
    Low = 2+ check-in-uri pe 7+ zile (estimare grosieră)
    """
    if n_checkins >= 10 and n_days >= 30:
        return "high"
    if n_checkins >= 5 and n_days >= 14:
        return "medium"
    return "low"


# ─────────────────────────────────────────────────────────────────────────────
#  MODUL 1: TDEE REAL (Energy Balance Inverse)
# ─────────────────────────────────────────────────────────────────────────────

def compute_real_tdee(
    checkins: list[dict],
    target_kcal: int,
    formula_tdee: int | None,
) -> dict:
    """
    Derivă TDEE-ul real din bilanțul energetic.

    Args:
        checkins    : lista de check-in-uri [{date, weight_kg}], sortată ASC
        target_kcal : caloriile zilnice pe care le consumă utilizatorul
        formula_tdee: TDEE calculat prin Mifflin/Katch (pentru comparație)

    Returns:
        dict cu estimated, formula_tdee, difference, confidence, days_tracked
    """
    n     = len(checkins)
    days  = _days_between(checkins[0]["date"], checkins[-1]["date"])
    conf  = _compute_confidence(n, days)

    weight_delta     = checkins[-1]["weight_kg"] - checkins[0]["weight_kg"]
    total_kcal_delta = weight_delta * KCAL_PER_KG
    daily_delta      = total_kcal_delta / days

    # TDEE_real = calorii mâncate − bilanțul zilnic
    # Dacă ai pierdut 0.5 kg/zi în deficit → TDEE mai mare decât target
    estimated = round(target_kcal - daily_delta)

    result = {
        "estimated":   estimated,
        "confidence":  conf,
        "days_tracked": days,
        "weight_delta": round(weight_delta, 2),
    }

    if formula_tdee:
        result["formula_tdee"] = formula_tdee
        result["difference"]   = estimated - formula_tdee  # pozitiv = metabolismul e mai rapid decât formula

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  MODUL 2: TREND REAL vs TARGET
# ─────────────────────────────────────────────────────────────────────────────

def compute_trend(checkins: list[dict], goal: str) -> dict:
    """
    Calculează rata reală de schimbare săptămânală vs rata targetată.

    Returns:
        direction           : "down" | "up" | "stable"
        rate_kg_per_week    : rata reală (kg/săpt., negativă = slăbire)
        target_rate         : rata așteptată din goal
        on_track            : True dacă devierea e < 35% față de target
        deviation_pct       : cât de departe e de target (%)
        weeks_tracked       : câte săptămâni de date avem
        progress_pct        : % din target atins (util pentru UI progress bar)
    """
    days  = _days_between(checkins[0]["date"], checkins[-1]["date"])
    weeks = days / 7

    weight_delta  = checkins[-1]["weight_kg"] - checkins[0]["weight_kg"]
    rate_per_week = round(weight_delta / weeks, 3) if weeks > 0 else 0.0
    target_rate   = GOAL_TARGET_RATES.get(goal, 0.0)

    # Direcție
    if rate_per_week < -0.05:
        direction = "down"
    elif rate_per_week > 0.05:
        direction = "up"
    else:
        direction = "stable"

    # Deviere față de target
    if target_rate == 0:
        on_track      = abs(rate_per_week) < 0.15
        deviation_pct = 0.0
        progress_pct  = 100 if on_track else 50
    else:
        deviation_pct = round(((rate_per_week - target_rate) / abs(target_rate)) * 100, 1)
        on_track      = abs(deviation_pct) <= 35
        # Progress bar: 100% = exact pe target, 0% = deloc progres
        if target_rate < 0:  # cut
            progress_pct = max(0, min(100, round((rate_per_week / target_rate) * 100)))
        else:  # bulk
            progress_pct = max(0, min(100, round((rate_per_week / target_rate) * 100)))

    return {
        "direction":           direction,
        "rate_kg_per_week":    rate_per_week,
        "target_rate":         target_rate,
        "on_track":            on_track,
        "deviation_pct":       deviation_pct,
        "weeks_tracked":       round(weeks, 1),
        "progress_pct":        progress_pct,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  MODUL 3: DETECȚIE STAGNARE
# ─────────────────────────────────────────────────────────────────────────────

def detect_stagnation(checkins: list[dict], goal: str) -> dict:
    """
    Stagnarea e detectată când:
      - Ultimele N check-in-uri (minim 3) sunt în ±0.4 kg
      - Și aceste check-in-uri acoperă minim 7 zile
      - Și obiectivul NU e menținere

    La menținere, 'stagnarea' e de fapt succesul.
    """
    if goal == "mentinere":
        return {"detected": False, "reason": "Menținere — stabilitatea e scopul."}

    if len(checkins) < 3:
        return {"detected": False, "reason": "Date insuficiente (min 3 check-in-uri)."}

    # Analizăm ultimele 4 check-in-uri (sau câte avem)
    recent = checkins[-4:] if len(checkins) >= 4 else checkins[-3:]
    weights  = [c["weight_kg"] for c in recent]
    variance = max(weights) - min(weights)
    span     = _days_between(recent[0]["date"], recent[-1]["date"])

    detected = variance <= 0.4 and span >= 7

    return {
        "detected":          detected,
        "variance_kg":       round(variance, 2),
        "days_span":         span,
        "checkins_analyzed": len(recent),
        "weights_recent":    weights,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  MODUL 4: RECOMANDARE ADAPTIVĂ
# ─────────────────────────────────────────────────────────────────────────────

def build_recommendation(
    trend: dict,
    stagnation: dict,
    current_target: int,
    goal: str,
) -> dict:
    """
    Generează recomandarea principală.

    Ierarhie de prioritate:
      1. Stagnare detectată              → ajustare imediată (±150 kcal)
      2. Deviere mare de trend (>35%)    → ajustare ușoară (±100 kcal)
      3. Pe track sau menținere          → păstrează planul

    Returns:
        action             : "maintain" | "reduce" | "increase"
        calorie_adjustment : delta față de target curent (negativă = reducere)
        new_target         : noul target calculat
        reason             : explicație în română, specifică cu cifre
        urgency            : "none" | "low" | "high"
        trigger            : ce a declanșat recomandarea
    """
    goal_dir = GOAL_TARGET_RATES.get(goal, 0.0)

    # ── 1. STAGNARE ───────────────────────────────────────────────────────────
    if stagnation.get("detected"):
        if goal_dir < 0:
            adj    = -150
            action = "reduce"
            reason = (
                f"Greutatea ta n-a mai scăzut de {stagnation['days_span']} zile "
                f"(variație de doar {stagnation['variance_kg']} kg). "
                f"Reducem cu 150 kcal/zi → nouă țintă: {current_target + adj} kcal."
            )
        elif goal_dir > 0:
            adj    = +150
            action = "increase"
            reason = (
                f"Stagnare la masă musculară detectată ({stagnation['days_span']} zile). "
                f"Creștem cu 150 kcal/zi → nouă țintă: {current_target + adj} kcal."
            )
        else:
            adj, action = 0, "maintain"
            reason = "La menținere, stabilitatea greutății este obiectivul. Totul e bine."

        return {
            "action":             action,
            "calorie_adjustment": adj,
            "new_target":         current_target + adj,
            "reason":             reason,
            "urgency":            "high",
            "trigger":            "stagnation",
        }

    # ── 2. DEVIERE MARE ───────────────────────────────────────────────────────
    if trend and not trend.get("on_track") and trend.get("weeks_tracked", 0) >= 2:
        rate        = trend["rate_kg_per_week"]
        target_rate = trend["target_rate"]
        dev         = trend["deviation_pct"]

        if goal_dir < 0:   # obiectiv: slăbire
            if rate > target_rate * 0.5:   # slăbește prea încet
                adj    = -100
                action = "reduce"
                reason = (
                    f"Slăbești cu {abs(rate):.2f} kg/săpt., față de targetul de "
                    f"{abs(target_rate):.1f} kg/săpt. ({dev:+.0f}% față de plan). "
                    f"−100 kcal/zi → nouă țintă: {current_target - 100} kcal."
                )
            elif rate < target_rate * 1.6:  # slăbește mult prea rapid
                adj    = +100
                action = "increase"
                reason = (
                    f"Slăbești mai rapid decât planificat ({abs(rate):.2f} kg/săpt.). "
                    f"Adăugăm 100 kcal/zi pentru a proteja masa musculară → {current_target + 100} kcal."
                )
            else:
                adj, action, reason = 0, "maintain", "Progres consistent. Menții planul."
        elif goal_dir > 0:  # obiectiv: masă musculară
            if rate < target_rate * 0.5:   # crește prea lent
                adj    = +100
                action = "increase"
                reason = (
                    f"Câștigi {rate:.2f} kg/săpt., sub targetul de {target_rate:.1f} kg/săpt. "
                    f"Adăugăm 100 kcal/zi → nouă țintă: {current_target + 100} kcal."
                )
            else:
                adj, action, reason = 0, "maintain", "Progres bun la masă. Menții planul."
        else:
            adj, action, reason = 0, "maintain", "La menținere, variațiile mici sunt normale."

        return {
            "action":             action,
            "calorie_adjustment": adj,
            "new_target":         current_target + adj,
            "reason":             reason,
            "urgency":            "low",
            "trigger":            "trend_deviation",
        }

    # ── 3. PE TRACK ───────────────────────────────────────────────────────────
    if trend:
        rate        = trend["rate_kg_per_week"]
        target_rate = trend["target_rate"]
        prog        = trend.get("progress_pct", 100)
        if goal_dir == 0:
            reason = f"Greutatea ta e stabilă ({rate:+.2f} kg/săpt.). Menținerea funcționează perfect."
        else:
            reason = (
                f"Progresezi cu {rate:+.2f} kg/săpt. față de targetul de "
                f"{target_rate:+.1f} kg/săpt. ({prog}% din plan). Continuă exact așa."
            )
    else:
        reason = "Loghează mai multe check-in-uri pentru o analiză precisă."

    return {
        "action":             "maintain",
        "calorie_adjustment": 0,
        "new_target":         current_target,
        "reason":             reason,
        "urgency":            "none",
        "trigger":            "on_track",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIA PRINCIPALĂ — apelată din main.py
# ─────────────────────────────────────────────────────────────────────────────

def run_adaptive_analysis(
    checkins:     list[dict],
    profile:      dict | None,
    last_session: dict | None,
    actual_kcal:  int | None = None,  # NOU: calorii REALE din food_logs
) -> dict:
    """
    Rulează toate cele 4 module și returnează analiza completă.

    Args:
        checkins     : din get_checkins(), sortate ASC
        profile      : din get_profile()
        last_session : prima intrare din get_user_sessions(limit=1)

    Returns:
        dict complet cu has_enough_data + toate modulele
    """
    n    = len(checkins)
    days = _days_between(checkins[0]["date"], checkins[-1]["date"]) if n >= 2 else 0

    # ── Verificare date minime ────────────────────────────────────────────────
    if n < MIN_CHECKINS:
        return {
            "has_enough_data":     False,
            "checkins_count":      n,
            "days_tracked":        days,
            "min_checkins_needed": MIN_CHECKINS,
            "min_days_needed":     MIN_DAYS,
            "insufficient_reason": f"Ai {n} check-in{'uri' if n != 1 else ''}. Motorul adaptiv pornește la {MIN_CHECKINS}.",
        }

    if days < MIN_DAYS:
        return {
            "has_enough_data":     False,
            "checkins_count":      n,
            "days_tracked":        days,
            "min_checkins_needed": MIN_CHECKINS,
            "min_days_needed":     MIN_DAYS,
            "insufficient_reason": f"Ai {days} zi{'le' if days != 1 else ''} de date. Motorul pornește la {MIN_DAYS}.",
        }

    # ── Extrage datele necesare ───────────────────────────────────────────────
    goal         = (profile or {}).get("goal", "mentinere")
    target_kcal  = (last_session or {}).get("target_kcal")
    formula_tdee = (last_session or {}).get("tdee")

    # ── Rulează modulele ──────────────────────────────────────────────────────
    # Bridge: folosim caloriile REALE dacă disponibile, altfel targetul asumat
    consumed_kcal = actual_kcal if actual_kcal is not None else target_kcal

    real_tdee  = (
        compute_real_tdee(checkins, consumed_kcal, formula_tdee)
        if target_kcal else
        {"estimated": None, "confidence": "insufficient", "reason": "Niciun calcul TDEE efectuat."}
    )

    trend       = compute_trend(checkins, goal)
    stagnation  = detect_stagnation(checkins, goal)
    recommend   = build_recommendation(
        trend=trend,
        stagnation=stagnation,
        current_target=target_kcal or 2000,
        goal=goal,
    )

    return {
        "has_enough_data": True,
        "checkins_count":  n,
        "days_tracked":    days,
        "goal":            goal,
        "current_target":  target_kcal,
        "real_tdee":       real_tdee,
        "trend":           trend,
        "stagnation":      stagnation,
        "recommendation":  recommend,
        "food_data_source": "food_logs_actual" if actual_kcal else "target_kcal_assumed",
        "actual_kcal_used": consumed_kcal,
    }