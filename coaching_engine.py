# =============================================================================
#  COACH ENGINE v1.2
#  Noian Cristian — AI Personal Coaching System
#  -----------------------------------------------------------------------------
#  v1.0-v1.1: calculate_bmr, calculate_tdee, calculate_target_calories,
#              calculate_macros, calculate_weekly_change, generate_report
#  v1.2 NOU:  BODY COMPOSITION MODULE — Katch-McArdle + estimare vizuală BF%
#             estimate_bf_from_body_type, calculate_lbm,
#             calculate_bmr_katch_mcardle, select_bmr_formula
# =============================================================================


# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTE GLOBALE
# ─────────────────────────────────────────────────────────────────────────────

ACTIVITY_MULTIPLIERS: dict[str, float] = {
    "sedentar":       1.200,
    "usor_activ":     1.375,
    "moderat_activ":  1.550,
    "foarte_activ":   1.725,
    "extrem_activ":   1.900,
}

GOAL_ADJUSTMENTS: dict[str, int] = {
    "cut_bland":   -200,
    "cut":         -400,
    "mentinere":      0,
    "bulk_lean":   +200,
    "bulk":        +400,
}

KCAL_PER_GRAM: dict[str, int] = {
    "protein": 4,
    "carbs":   4,
    "fat":     9,
}

PROTEIN_RATIO: float = 2.0
FAT_RATIO:     float = 0.9
KCAL_PER_KG_FAT: int = 7700


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIA 1: BMR — Mifflin-St Jeor (formula clasică, păstrată intact)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_bmr(weight_kg: float, height_cm: float, age: int, sex: str) -> float:
    """
    Rata Metabolică Bazală prin Mifflin-St Jeor.
    Notă: supraestimează pentru BF% ridicat. Folosește select_bmr_formula()
    care alege automat Katch-McArdle când sunt disponibile date de compoziție.
    """
    base: float = (10 * weight_kg) + (6.25 * height_cm) - (5 * age)
    if sex.lower() == "m":
        return round(base + 5, 2)
    elif sex.lower() == "f":
        return round(base - 161, 2)
    else:
        raise ValueError(f"Sex invalid: '{sex}'. Valori acceptate: 'm' sau 'f'.")


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIA 2: TDEE
# ─────────────────────────────────────────────────────────────────────────────

def calculate_tdee(bmr: float, activity_level: str) -> float:
    multiplier = ACTIVITY_MULTIPLIERS.get(activity_level.lower())
    if multiplier is None:
        raise ValueError(
            f"Nivel activitate invalid: '{activity_level}'. "
            f"Opțiuni: {', '.join(ACTIVITY_MULTIPLIERS.keys())}"
        )
    return round(bmr * multiplier, 2)


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIA 3: CALORII ȚINTĂ
# ─────────────────────────────────────────────────────────────────────────────

def calculate_target_calories(tdee: float, goal: str) -> float:
    adjustment = GOAL_ADJUSTMENTS.get(goal.lower())
    if adjustment is None:
        raise ValueError(
            f"Obiectiv invalid: '{goal}'. "
            f"Opțiuni: {', '.join(GOAL_ADJUSTMENTS.keys())}"
        )
    return round(tdee + adjustment, 2)


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIA 4: MACRONUTRIENȚI
# ─────────────────────────────────────────────────────────────────────────────

def calculate_macros(weight_kg: float, target_calories: float) -> dict:
    protein_g    = round(PROTEIN_RATIO * weight_kg, 1)
    fat_g        = round(FAT_RATIO     * weight_kg, 1)
    protein_kcal = protein_g * KCAL_PER_GRAM["protein"]
    fat_kcal     = fat_g     * KCAL_PER_GRAM["fat"]
    remaining    = target_calories - protein_kcal - fat_kcal

    if remaining < 0:
        raise ValueError(
            f"Calorii rămase negative ({remaining:.0f} kcal). "
            f"Ținta ({target_calories:.0f}) e prea mică față de P+F ({protein_kcal + fat_kcal:.0f} kcal)."
        )

    carbs_g    = round(remaining / KCAL_PER_GRAM["carbs"], 1)
    carbs_kcal = round(carbs_g * KCAL_PER_GRAM["carbs"], 1)
    total_kcal = round(protein_kcal + fat_kcal + carbs_kcal, 1)

    return {
        "protein_g":    protein_g,
        "fat_g":        fat_g,
        "carbs_g":      carbs_g,
        "protein_kcal": protein_kcal,
        "fat_kcal":     fat_kcal,
        "carbs_kcal":   carbs_kcal,
        "total_kcal":   total_kcal,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIA 5: ESTIMARE SCHIMBARE SĂPTĂMÂNALĂ
# ─────────────────────────────────────────────────────────────────────────────

def calculate_weekly_change(goal: str) -> dict:
    adjustment  = GOAL_ADJUSTMENTS.get(goal.lower(), 0)
    weekly_kcal = adjustment * 7
    kg_per_week = round(weekly_kcal / KCAL_PER_KG_FAT, 2)

    if adjustment < 0:
        direction = "pierdere"
        display   = f"~{abs(kg_per_week)} kg/săpt. pierdut"
    elif adjustment > 0:
        direction = "câștig"
        display   = f"~{abs(kg_per_week)} kg/săpt. acumulat"
    else:
        direction = "menținere"
        display   = "~0 kg/săpt. (recompoziție)"

    return {"kg_per_week": kg_per_week, "direction": direction, "display": display}


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIA 6: RAPORT CONSOLĂ
# ─────────────────────────────────────────────────────────────────────────────

def generate_report(
    name: str, weight_kg: float, height_cm: float,
    age: int, sex: str, activity_level: str, goal: str,
    body_type: str | None = None, body_fat_pct: float | None = None,
) -> None:
    bmr, formula, lbm, bf = select_bmr_formula(
        weight_kg, height_cm, age, sex, body_fat_pct, body_type
    )
    tdee            = calculate_tdee(bmr, activity_level)
    target_calories = calculate_target_calories(tdee, goal)
    macros          = calculate_macros(weight_kg, target_calories)
    weekly          = calculate_weekly_change(goal)

    SEP = "═" * 56
    S2  = "─" * 56
    print(f"\n{SEP}")
    print(f"  RAPORT COACHING ── {name.upper()}")
    print(SEP)
    print(f"\n  {'BMR':<24} {bmr:>8.0f} kcal/zi  [{formula}]")
    if lbm:
        print(f"  {'LBM (masă slabă)':<24} {lbm:>8.1f} kg  [BF%: {bf:.1f}%]")
    print(f"  {'TDEE':<24} {tdee:>8.0f} kcal/zi")
    print(f"  {'Țintă zilnică':<24} {target_calories:>8.0f} kcal/zi")
    print(f"  {'Estimare':<24} {weekly['display']}")
    print(f"\n  {'🥩 Proteină':<24} {macros['protein_g']:>6.1f} g")
    print(f"  {'🫒 Grăsimi':<24} {macros['fat_g']:>6.1f} g")
    print(f"  {'🍚 Carbohidrați':<24} {macros['carbs_g']:>6.1f} g")
    print(f"\n{SEP}\n")


# ═══════════════════════════════════════════════════════════════════════════════
#  MODUL COMPOZIȚIE CORPORALĂ — v1.2 (ADĂUGAT, nu modifică nimic de sus)
#
#  Rezolvă problema critică: Mifflin supraestimează grav pentru BF% ridicat.
#  Explicație: grăsimea e metabolic inertă (~2 kcal/g/zi vs ~13 kcal/g/zi mușchi).
#  Soluție: formula Katch-McArdle care operează pe Lean Body Mass (LBM).
#
#  Interfață publică:
#    select_bmr_formula() — funcția centrală, apelată din main.py
# ═══════════════════════════════════════════════════════════════════════════════

# Mapare categorie vizuală → BF% estimat, calibrat pe sex
# Surse: ACSM Guidelines, Gallagher et al. (2000), Jackson & Pollock norms
BODY_TYPE_BF_MAP: dict[str, dict[str, float]] = {
    # Cheie              Masculin   Feminin
    "very_lean":        {"m": 10.0, "f": 18.0},  # definit, vascularitate vizibilă
    "lean":             {"m": 15.0, "f": 22.0},  # fit, formă atletică
    "average":          {"m": 22.0, "f": 28.0},  # mediu, aspect sănătos
    "slightly_over":    {"m": 28.0, "f": 34.0},  # grăsime vizibilă, definire absentă
    "overweight":       {"m": 34.0, "f": 40.0},  # supraponderal clar
    "obese":            {"m": 42.0, "f": 47.0},  # obezitate semnificativă
}


def estimate_bf_from_body_type(body_type: str, sex: str) -> float:
    """
    Convertește selecția vizuală în BF% estimat, diferențiat pe sex.
    Returnează midpoint-ul rangului clinic corespunzător categoriei.
    """
    sex_key = "m" if sex.lower() == "m" else "f"
    entry   = BODY_TYPE_BF_MAP.get(body_type)
    if entry is None:
        raise ValueError(
            f"Tip corp necunoscut: '{body_type}'. "
            f"Opțiuni valide: {list(BODY_TYPE_BF_MAP.keys())}"
        )
    return entry[sex_key]


def calculate_lbm(weight_kg: float, bf_pct: float) -> float:
    """
    Lean Body Mass = greutate totală minus masă grasă.
    LBM = weight_kg × (1 − BF% / 100)
    
    Aceasta este masa metabolic activă — singurul predictor real al BMR.
    """
    if not (3.0 <= bf_pct <= 65.0):
        raise ValueError(
            f"BF% = {bf_pct}% este în afara intervalului fiziologic valid (3–65%)."
        )
    return round(weight_kg * (1.0 - bf_pct / 100.0), 2)


def calculate_bmr_katch_mcardle(lbm_kg: float) -> float:
    """
    Formula Katch-McArdle:  BMR = 370 + (21.6 × LBM_kg)
    
    De ce e superioară Mifflin pentru compoziții variate:
    - Nu supraevaluează obezii (grăsimea nu contribuie la BMR real)
    - Nu subevaluează atleții (masa musculară ridicată → BMR ridicat)
    - Un individ de 92kg cu 35% BF → LBM = 59.8kg → BMR = 1661 kcal
      vs Mifflin pentru 92kg/182cm/25ani/M → BMR = 1967 kcal (+306 kcal eroare!)
    """
    return round(370.0 + (21.6 * lbm_kg), 2)


def select_bmr_formula(
    weight_kg:  float,
    height_cm:  float,
    age:        int,
    sex:        str,
    bf_pct:     float | None = None,
    body_type:  str   | None = None,
) -> tuple[float, str, float | None, float | None]:
    """
    Selector inteligent de formulă BMR — funcția centrală a modulului v1.2.
    
    Logică de selecție (în ordine de prioritate):
      1. bf_pct furnizat direct → Katch-McArdle cu BF% exact
      2. body_type selectat vizual → estimare BF% → Katch-McArdle  
      3. Niciun input de compoziție → Mifflin-St Jeor (comportament v1.0-v1.1)
    
    Args:
        weight_kg, height_cm, age, sex : date antropometrice standard
        bf_pct    : procent grăsime cunoscut direct (opțional)
        body_type : cheie din BODY_TYPE_BF_MAP, din selecția vizuală (opțional)
    
    Returns:
        tuple: (bmr_kcal, formula_name, lbm_kg_or_None, effective_bf_pct_or_None)
    """
    effective_bf: float | None = None
    lbm:          float | None = None

    if bf_pct is not None:
        effective_bf = bf_pct                                    # direct, exact
    elif body_type is not None:
        effective_bf = estimate_bf_from_body_type(body_type, sex)  # estimat vizual

    if effective_bf is not None:
        lbm       = calculate_lbm(weight_kg, effective_bf)
        bmr       = calculate_bmr_katch_mcardle(lbm)
        formula   = "Katch-McArdle"
    else:
        bmr       = calculate_bmr(weight_kg, height_cm, age, sex)  # fallback clasic
        formula   = "Mifflin-St Jeor"

    return (bmr, formula, lbm, effective_bf)


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN — Test local
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=== Test Mifflin (fără compoziție) ===")
    generate_report(
        name="Cristian", weight_kg=75.0, height_cm=178.0,
        age=19, sex="m", activity_level="foarte_activ", goal="bulk_lean",
    )

    print("=== Test Katch-McArdle (cu selecție vizuală) ===")
    generate_report(
        name="Test BF Mare", weight_kg=92.0, height_cm=182.0,
        age=25, sex="m", activity_level="sedentar", goal="cut",
        body_type="overweight",  # ~34% BF → LBM = 60.7kg → BMR = 1681 kcal
    )

    print("=== Test Katch-McArdle (BF% direct) ===")
    generate_report(
        name="Client F", weight_kg=65.0, height_cm=165.0,
        age=35, sex="f", activity_level="usor_activ", goal="cut_bland",
        body_fat_pct=28.0,  # știe exact
    )

if __name__ == "__main__":
    main()