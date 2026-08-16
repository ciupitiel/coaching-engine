#!/usr/bin/env python3
"""
patch_main.py — Integrează Morning Plan Router în main.py
Rulează O SINGURĂ DATĂ din directorul proiectului:
    python patch_main.py

Aplică 3 inserții:
  1. Import: from main_morning_additions import init_morning_router
             from database_morning_plan import init_db_morning_plan
  2. lifespan(): await init_db_morning_plan()
  3. Routers:  app.include_router(init_morning_router())
"""

import sys

FILE = "main.py"


# ─── Inserție 1: importuri ────────────────────────────────────────────────────

P1_OLD = "from main_push_additions import init_push_router"
P1_NEW = (
    "from main_push_additions import init_push_router\n"
    "from main_morning_additions import init_morning_router\n"
    "from database_morning_plan import init_db_morning_plan"
)


# ─── Inserție 2: lifespan — init_db_morning_plan după init_db_templates ──────

P2_OLD = "    await init_db_templates() # #18: meal_templates"
P2_NEW = (
    "    await init_db_templates() # #18: meal_templates\n"
    "    await init_db_morning_plan()  # Morning Plans"
)


# ─── Inserție 3: router — după init_templates_router ─────────────────────────

P3_OLD = "app.include_router(init_templates_router()) # #18: Meal Templates"
P3_NEW = (
    "app.include_router(init_templates_router()) # #18: Meal Templates\n"
    "app.include_router(init_morning_router())   # Morning Plan Confirm"
)


# ─────────────────────────────────────────────────────────────────────────────

def apply(content: str, old: str, new: str, label: str) -> str:
    if old not in content:
        print(f"\n❌  {label}: textul de înlocuit NU a fost găsit.")
        print(f"    Primele 80 de caractere căutate: {repr(old[:80])}")
        sys.exit(1)
    updated = content.replace(old, new, 1)
    print(f"✅  {label}")
    return updated


def main() -> None:
    print(f"patch_main.py — target: {FILE}\n")

    try:
        with open(FILE, "r", encoding="utf-8") as f:
            src = f.read()
    except FileNotFoundError:
        print(f"❌  {FILE} nu a fost găsit. Rulează din directorul rădăcină al proiectului.")
        sys.exit(1)

    src = apply(src, P1_OLD, P1_NEW, "Inserție 1 · importuri morning")
    src = apply(src, P2_OLD, P2_NEW, "Inserție 2 · lifespan init_db_morning_plan")
    src = apply(src, P3_OLD, P3_NEW, "Inserție 3 · app.include_router morning")

    with open(FILE, "w", encoding="utf-8") as f:
        f.write(src)

    print(f"\n✅  {FILE} actualizat · 3 inserții aplicate cu succes.")
    print("    Poți șterge patch_main.py — nu mai e necesar.")


if __name__ == "__main__":
    main()