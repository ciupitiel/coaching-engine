# =============================================================================
#  Dockerfile — Coaching Engine · Etapa 3
#  Noian Cristian · Bazat pe inteligență artificială
#  -----------------------------------------------------------------------------
#  v1.1 FIX: eliminat "COPY food_adaptive_bridge.py ." redundant
#             (era deja inclus în "COPY *.py .")
#
#  Build:  docker build -t coaching-engine .
#  Run:    docker run -p 8000:8000 --env-file .env coaching-engine
#
#  De ce python:3.11-slim?
#    · slim = imagine minimală (~50MB vs ~900MB full) — build mai rapid pe Render
#    · 3.11 = stabil, toate dependențele au wheels pre-built (fără compilare lungă)
#
#  De ce apt install gcc + fonts-dejavu?
#    · gcc: necesar dacă asyncpg nu găsește wheel pre-built pentru platformă
#    · fonts-dejavu: instalează DejaVuSans.ttf la /usr/share/fonts/truetype/dejavu/
#      → main.py găsește fontul automat la acea cale → PDF-uri cu diacritice corecte
# =============================================================================

FROM python:3.11-slim

# Directorul de lucru în container — toate fișierele vor fi aici
WORKDIR /app

# ── Dependențe sistem ──────────────────────────────────────────────────────────
# Instalate ÎNAINTE de pip install pentru că asyncpg poate necesita gcc
# --no-install-recommends: minimizează dimensiunea imaginii
# rm -rf /var/lib/apt/lists/*: șterge cache apt după instalare (~30MB economisit)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# ── Dependențe Python ──────────────────────────────────────────────────────────
# Copiem DOAR requirements.txt primul — Docker cache:
# dacă requirements.txt nu s-a schimbat, acest layer e reutilizat din cache
# → build de 30s în loc de 3 minute la fiecare schimbare de cod
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Codul aplicației ───────────────────────────────────────────────────────────
# Copiem după pip install — orice schimbare de cod nu invalidează layer-ul de pip
COPY *.py .
COPY *.html .
COPY gdpr.js .

# ── Port ──────────────────────────────────────────────────────────────────────
# 8000 = portul implicit local
# Pe Render.com, $PORT e setat automat de platformă (de obicei 10000)
EXPOSE 8000

# ── Pornire ───────────────────────────────────────────────────────────────────
# Shell form (nu exec form) — permite expandarea variabilei $PORT
# ${PORT:-8000} = folosește $PORT dacă există, altfel 8000 (local)
# --host 0.0.0.0 = acceptă conexiuni din exterior (obligatoriu în container)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}