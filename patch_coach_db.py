#!/usr/bin/env python3
"""
patch_coach_db.py — Adaugă init_db_coach_v2() în lifespan()
python patch_coach_db.py
"""
import sys

FILE = "main.py"

P1_OLD = "from database_coach_v2 import" if False else None  # skip dacă există deja

P_IMPORT_OLD = "from database_morning_plan import init_db_morning_plan"
P_IMPORT_NEW = (
    "from database_morning_plan import init_db_morning_plan\n"
    "from database_coach_v2 import init_db_coach_v2"
)

P_LIFESPAN_OLD = "    await init_db_morning_plan()  # Morning Plans"
P_LIFESPAN_NEW = (
    "    await init_db_morning_plan()  # Morning Plans\n"
    "    await init_db_coach_v2()      # Coach Recommendations"
)

def apply(content, old, new, label):
    if old not in content:
        print(f"❌  {label}: nu am găsit textul — posibil deja aplicat")
        sys.exit(1)
    print(f"✅  {label}")
    return content.replace(old, new, 1)

def main():
    try:
        with open(FILE, "r", encoding="utf-8") as f:
            src = f.read()
    except FileNotFoundError:
        print(f"❌  {FILE} nu a fost găsit.")
        sys.exit(1)

    if "from database_coach_v2 import init_db_coach_v2" in src:
        print("ℹ️  Import deja prezent — skip import patch")
    else:
        src = apply(src, P_IMPORT_OLD, P_IMPORT_NEW, "Import init_db_coach_v2")

    if "await init_db_coach_v2()" in src:
        print("ℹ️  init_db_coach_v2() deja în lifespan — skip")
    else:
        src = apply(src, P_LIFESPAN_OLD, P_LIFESPAN_NEW, "lifespan init_db_coach_v2")

    with open(FILE, "w", encoding="utf-8") as f:
        f.write(src)

    print(f"\n✅  {FILE} actualizat.")

if __name__ == "__main__":
    main()