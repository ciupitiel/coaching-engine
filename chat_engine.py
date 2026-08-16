# =============================================================================
#  chat_engine.py — AI Chat Coach · Context Builder
#  Noian Cristian · Bazat pe inteligență artificială
#  P2: Construiește system prompt-ul contextual complet pentru /chat
#  -----------------------------------------------------------------------------
#  v1.1 FIX: cheia "strict" redenumită în "militar" în PERSONA_PROMPTS
#  — anterior persona "Militar" din Settings nu funcționa niciodată
#    (PERSONA_PROMPTS.get("militar") → None → fallback la "empatic")
# =============================================================================

GOAL_MAP = {
    "cut_bland": "Tăiere Blândă (−200 kcal/zi · ~0.2 kg/săpt.)",
    "cut":       "Tăiere Moderată (−400 kcal/zi · ~0.4 kg/săpt.)",
    "mentinere": "Menținere / Recompoziție",
    "bulk_lean": "Lean Bulk (+200 kcal/zi · ~0.2 kg/săpt.)",
    "bulk":      "Bulk Moderat (+400 kcal/zi · ~0.4 kg/săpt.)",
}

ACT_MAP = {
    "sedentar":      "Sedentar (fără sport)",
    "usor_activ":    "Ușor activ (1–3 zile/săpt.)",
    "moderat_activ": "Moderat activ (3–5 zile/săpt.)",
    "foarte_activ":  "Foarte activ (6–7 zile/săpt.)",
    "extrem_activ":  "Extrem de activ (antrenamente duble sau muncă fizică)",
}

# P5 — Blocuri de ton per personaj AI (injectate în system prompt de build_coach_system_prompt)
# FIX: cheia era "strict" → corectată în "militar" (Settings salvează "militar", nu "strict")
PERSONA_PROMPTS: dict[str, list[str]] = {
    "empatic": [
        "TON SETAT DE USER → EMPATIC:",
        "Ești cald, înțelegător și motivational. Recunoști efortul real.",
        "Limbaj natural: 'Înțeleg că...', 'Ai făcut bine că...', 'Hai să vedem împreună...'",
        "Critică constructiv — spui CE trebuie schimbat, dar cu căldură și context.",
        "Nu ești terapeut. Ești prietenul pregătit care spune adevărul cu empatie.",
        "",
    ],
    "stiintific": [
        "TON SETAT DE USER → ȘTIINȚIFIC:",
        "Ești precis, bazat pe date, tehnic. Fiecare afirmație are un mecanism explicat.",
        "Limbaj: 'Conform datelor tale...', 'Mecanismul este...', 'Calculul arată...'",
        "Citezi cifre exacte. Explici cauzalitatea (deficit caloric → oxidare grăsimi).",
        "Zero afirmații vagi sau fără suport numeric. Zero generalizări.",
        "",
    ],
    "militar": [  # FIX: era "strict" — acum "militar" ca în database_settings.py și UI
        "TON SETAT DE USER → MILITAR:",
        "Ești direct, ferm, fără scuze. Disciplina produce rezultate. Scuzele nu.",
        "Limbaj: 'Ești responsabil de...', 'Execuți sau nu execuți.', 'Fără excepții.'",
        "Empatie zero față de scuze. Empatie totală față de efort real și consecvență.",
        "Spui exact ce trebuie făcut. O singură dată. Clientul decide dacă execută.",
        "",
    ],
}


def build_coach_system_prompt(
    profile: dict | None,
    checkins: list,
    summary: dict,
    last_session: dict | None,
    persona: str = "empatic",
) -> str:
    """
    Construiește system prompt-ul complet pentru AI Chat Coach.

    Toată informația clientului (profil, calcul TDEE, progres greutate)
    este injectată DIRECT în system prompt — AI-ul o știe de la 'naștere',
    fără să fie nevoie să întrebe utilizatorul nimic.

    Logică stagnare: dacă ultimele 3 check-in-uri sunt în ±0.5 kg,
    AI-ul e avertizat explicit și poate propune ajustări proactive.
    """

    lines = [
        "Ești un coach personal de nutriție și fitness.",
        "Vorbești DIRECT cu clientul tău — față în față, ca un prieten bine pregătit.",
        "Ești direct, cald, practic și proactiv. Nu ești un chatbot generic.",
        "",
        "REGULI ABSOLUTE (nicio excepție):",
        "R1 · Persoana a II-a MEREU: 'tu ai', 'ai scăzut', 'vei mânca' — NICIODATĂ 'clientul'.",
        "R2 · Dacă ai date de progres, citezi cifre EXACTE din context. Nu generaliza.",
        "R3 · Zero fraze goale: Felicitări!, Ești pe drumul cel bun!, Succes!, Bravo!",
        "R4 · Dacă lipsesc date (0 check-in-uri, 0 calcule), ghidezi activ clientul să le adauge.",
        "R5 · Ton de coach, nu de terapeut. Spui CE trebuie schimbat, nu doar că e bine.",
        "R6 · Maxim 3 paragrafe scurte. Pentru întrebări simple → 1 paragraf.",
        "R7 · Exclusiv română. Fără cuvinte englezești nemotivate.",
        "",
        "=" * 50,
        "DATE COMPLETE ALE CLIENTULUI",
        "=" * 50,
        "",
    ]
    # P5: Injectează tonul ales de user — imediat înainte de separatorul de date
    _p5_block = PERSONA_PROMPTS.get(persona, PERSONA_PROMPTS["empatic"])
    lines[lines.index("=" * 50):lines.index("=" * 50)] = _p5_block

    # ── Profil fizic ──────────────────────────────────────────
    if profile:
        sex_label = "Masculin" if profile.get("sex") == "m" else "Feminin"
        goal_label = GOAL_MAP.get(profile.get("goal", ""), profile.get("goal", "—"))
        act_label  = ACT_MAP.get(profile.get("activity_level", ""), profile.get("activity_level", "—"))

        lines += [
            "PROFIL FIZIC:",
            f"  Sex: {sex_label}  |  Vârstă: {profile.get('age')} ani  |  Înălțime: {profile.get('height_cm')} cm",
            f"  Greutate la înregistrare: {profile.get('initial_weight_kg')} kg",
            f"  Nivel activitate: {act_label}",
            f"  Obiectiv: {goal_label}",
            "",
        ]
    else:
        lines += [
            "PROFIL FIZIC: Niciun profil salvat.",
            "  → Recomandă să completeze calculatorul din pagina principală.",
            "",
        ]

    # ── Ultimul calcul TDEE ───────────────────────────────────
    if last_session:
        lines += [
            "ULTIMUL CALCUL TDEE:",
            f"  BMR: {last_session.get('bmr')} kcal  |  TDEE: {last_session.get('tdee')} kcal  |  Țintă zilnică: {last_session.get('target_kcal')} kcal",
            f"  Macros: Proteină {last_session.get('protein_g')}g  ·  Carbohidrați {last_session.get('carbs_g')}g  ·  Grăsimi {last_session.get('fat_g')}g",
            f"  Formulă folosită: {last_session.get('formula_used', 'Mifflin-St Jeor')}",
            "",
        ]
    else:
        lines += [
            "CALCUL TDEE: Niciun calcul efectuat încă.",
            "  → Recomandă să completeze calculatorul pentru a obține TDEE și macros personalizate.",
            "",
        ]

    # ── Progres greutate ──────────────────────────────────────
    total_ci = summary.get("total", 0)

    if total_ci > 0:
        first_kg = summary.get("first_kg")
        last_kg  = summary.get("last_kg")
        change   = round(last_kg - first_kg, 1)
        direction = "scăzut" if change < 0 else "crescut" if change > 0 else "rămas stabil"
        prefix   = "+" if change > 0 else ""

        lines += [
            f"PROGRES GREUTATE ({total_ci} check-in-uri):",
            f"  Prima înregistrare: {first_kg} kg  →  Ultima: {last_kg} kg",
            f"  Schimbare totală: {prefix}{change} kg (greutatea a {direction})",
            f"  Minim înregistrat: {summary.get('min_kg')} kg  |  Maxim: {summary.get('max_kg')} kg",
        ]

        # Ultimele 7 check-in-uri vizualizate cronologic
        if checkins:
            recent = checkins[-7:]
            recent_str = "  →  ".join(
                [f"{c['date'][5:]}: {c['weight_kg']} kg" for c in recent]
            )
            lines.append(f"  Trend recent: {recent_str}")

        # Detecție stagnare automată (±0.5 kg în ultimele 3 check-in-uri)
        if len(checkins) >= 3:
            last3 = [c["weight_kg"] for c in checkins[-3:]]
            if max(last3) - min(last3) <= 0.5:
                lines.append(
                    f"  ⚠ STAGNARE DETECTATĂ: ultimele 3 check-in-uri sunt în intervalul "
                    f"{min(last3)}–{max(last3)} kg. Consideră ajustare calorică sau de training."
                )

        lines.append("")
    else:
        lines += [
            "PROGRES GREUTATE: 0 check-in-uri înregistrate.",
            "  → Clientul nu a logat nicio greutate. Recomandă-i să folosească",
            "     secțiunea 'Check-in · Greutatea de Azi' din aplicație.",
            "",
        ]

    lines.append("=" * 50)
    lines.append("CONVERSAȚIE:")

    return "\n".join(lines)