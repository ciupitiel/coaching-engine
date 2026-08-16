#!/bin/bash
# ══════════════════════════════════════════════════════════════════
#  Noian Cristian · Bazat pe inteligență artificială
#  Launcher macOS — dublu-click pentru a porni serverul
# ══════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Citim .env linie cu linie — xargs (varianta veche) interpreta \n ca newline
# și rupea valorile complexe cum sunt VAPID_PRIVATE_KEY sau DATABASE_URL.
# Varianta nouă: IFS= read -r citește linia brută, export "linia" o exportă corect
# inclusiv cu spații și caractere speciale în valoare.
if [ -f "$SCRIPT_DIR/.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        # Ignoră linii goale și comentarii
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        # Ignoră linii fără = (nu sunt key=value)
        [[ "$line" != *=* ]] && continue
        # Exportă variabila — ghilimelele protejează valorile cu spații
        export "$line"
    done < "$SCRIPT_DIR/.env"
else
    echo "  ⚠️  Fișierul .env nu a fost găsit în $SCRIPT_DIR"
    echo "  Creează-l cu: GROQ_API_KEY=..., DATABASE_URL=..., SECRET_KEY=..."
    echo "  Apasă ENTER pentru a continua oricum (serverul va porni fără variabile)."
    read
fi

clear

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║   Noian Cristian                         ║"
echo "  ║   Bazat pe inteligență artificială       ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""

# Verifică că venv există — dacă nu, îl creăm și instalăm dependențele
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "  📦  Prima pornire — creez mediul virtual și instalez dependențele..."
    echo "      (durează ~60 sec, doar prima dată)"
    echo ""
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt -q
    echo "  ✅  Dependențe instalate."
    echo ""
else
    source venv/bin/activate

    # Verifică că pachetele core sunt instalate
    if ! python3 -c "import fastapi, groq, uvicorn, asyncpg" 2>/dev/null; then
        echo "  📦  Dependențe lipsă — instalez..."
        pip install -r requirements.txt -q
        echo "  ✅  Dependențe actualizate."
        echo ""
    fi
fi

# IP-ul local pentru acces de pe telefon
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null)
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP=$(ipconfig getifaddr en1 2>/dev/null)
fi

echo "  ✅  Server activ la:"
echo ""
echo "       💻  Mac:     http://localhost:8000"
if [ -n "$LOCAL_IP" ]; then
    echo "       📱  Telefon: http://$LOCAL_IP:8000"
    echo "           (trebuie să fii pe același WiFi)"
fi
echo ""
echo "  ──────────────────────────────────────────"
echo "  Apasă CTRL+C pentru a opri serverul."
echo "  ══════════════════════════════════════════"
echo ""

# Deschide browser-ul automat după 1.5 secunde
(sleep 1.5 && open "http://localhost:8000") &

# Pornește serverul
uvicorn main:app --host 0.0.0.0 --port 8000
