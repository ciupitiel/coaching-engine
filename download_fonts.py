"""
download_fonts.py — Rulează o singură dată pentru a instala fonturile PDF.
Descarcă DejaVuSans din jsDelivr CDN (npm mirror) — extrem de fiabil.

Cum rulezi:
  cd Desktop/coaching_engine
  python3 download_fonts.py
"""

import urllib.request
import os
import sys

FONTS = {
    'DejaVuSans.ttf':      'https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans.ttf',
    'DejaVuSans-Bold.ttf': 'https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans-Bold.ttf',
}

MIN_SIZE = 50_000  # un font real are minim 50 KB; o pagina HTML are ~5-10 KB

print("\n  Descărcare fonturi DejaVu pentru PDF...\n")

for filename, url in FONTS.items():
    dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

    # Verifică dacă există deja și e valid
    if os.path.exists(dest):
        size = os.path.getsize(dest)
        if size >= MIN_SIZE:
            print(f"  ✓  {filename} există deja ({size // 1024} KB) — ok, sărim peste")
            continue
        else:
            print(f"  !  {filename} pare corupt ({size} bytes) — șterg și re-descarc...")
            os.remove(dest)

    # Descarcă
    try:
        print(f"  ↓  {filename} ...", end='', flush=True)
        urllib.request.urlretrieve(url, dest)
        size = os.path.getsize(dest)

        if size < MIN_SIZE:
            print(f"\n\n  EROARE: fișierul descărcat are doar {size} bytes.")
            print(f"  Probabil nu e un font real (poate e o pagină HTML de eroare).")
            print(f"  Verifică conexiunea la internet și încearcă din nou.\n")
            os.remove(dest)
            sys.exit(1)

        print(f" ✓  ({size // 1024} KB)")

    except Exception as e:
        print(f"\n\n  EROARE la descărcare: {e}\n")
        sys.exit(1)

print("\n  ✅  Ambele fonturi sunt instalate în folderul proiectului.")
print("  Repornește serverul cu dublu-click pe START.command și încearcă PDF-ul din nou.\n")
