import os
import datetime

import jwt
from fastapi import APIRouter, Depends
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth import require_user_email


# ─────────────────────────────────────────────────────────────────────────────
#  LIMITE ZILNICE — ajustează după nevoie
# ─────────────────────────────────────────────────────────────────────────────

DAILY_LIMITS: dict[str, int] = {
    "/chat":                25,   # mesaje chat AI
    "/chat/stream":         25,   # identic — versiunea streaming
    "/food/log":            40,   # loguri alimentare text
    "/food/voice":          15,   # loguri vocale (Whisper + Llama)
    "/meal-plan/generate":   5,   # planuri alimentare standard
    "/meal-plan/smart":      5,   # planuri RAG-enhanced
    "/adaptive/analysis":   10,   # analize adaptive AI (GET sync — main.py)
    "/adaptive/queue":      10,   # analize adaptive AI (POST async — main_p6_additions.py)
    "/calculate":           10,   # calcule TDEE cu coaching insight
    "/exercise/log":        50,   # loguri de antrenament AI (Groq call)
}

# Mesaje personalizate per endpoint (afișate în UI când limita e depășită)
_LIMIT_MESSAGES: dict[str, str] = {
    "/chat":                "Ai atins limita zilnică de 25 mesaje chat AI.",
    "/chat/stream":         "Ai atins limita zilnică de 25 mesaje chat AI.",
    "/food/log":            "Ai atins limita zilnică de 40 loguri alimentare AI.",
    "/food/voice":          "Ai atins limita zilnică de 15 loguri vocale.",
    "/meal-plan/generate":  "Ai atins limita zilnică de 5 planuri alimentare.",
    "/meal-plan/smart":     "Ai atins limita zilnică de 5 planuri alimentare RAG.",
    "/adaptive/analysis":   "Ai atins limita zilnică de 10 analize adaptive.",
    "/adaptive/queue":      "Ai atins limita zilnică de 10 analize adaptive.",
    "/calculate":           "Ai atins limita zilnică de 10 calcule TDEE.",
}


# ─────────────────────────────────────────────────────────────────────────────
#  STORE IN-MEMORY
#  Structură: { "YYYY-MM-DD": { "user@email.com": { "/path": count } } }
# ─────────────────────────────────────────────────────────────────────────────

_store: dict[str, dict[str, dict[str, int]]] = {}


def _today() -> str:
    return datetime.date.today().isoformat()


def _purge_old_days() -> None:
    """Curăță zilele vechi din store — apelat la fiecare request de pe endpoint limitat."""
    today = _today()
    for old_day in list(_store.keys()):
        if old_day != today:
            del _store[old_day]


def _ensure_user_slot(today: str, email: str, path: str) -> None:
    """Inițializează structura dacă nu există."""
    if today not in _store:
        _store[today] = {}
    if email not in _store[today]:
        _store[today][email] = {}
    if path not in _store[today][email]:
        _store[today][email][path] = 0


def _extract_email_from_request(request: Request) -> str | None:
    """
    Extrage emailul din Bearer token fără să creeze dependențe circulare.
    Eșec silențios → request trece mai departe (auth.py gestionează 401).
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token  = auth_header[7:].strip()
    secret = os.environ.get("SECRET_KEY", "cheie-secreta-foarte-sigura")

    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return payload.get("sub")
    except Exception:
        return None  # Token invalid/expirat — auth.py va returna 401


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚII PUBLICE
# ─────────────────────────────────────────────────────────────────────────────

def get_usage_for_email(email: str) -> dict:
    """
    Returnează statisticile de utilizare ale zilei curente pentru un user.
    Expus prin GET /rate-limits (endpoint opțional, util pentru UI).
    """
    today      = _today()
    user_usage = (_store.get(today) or {}).get(email, {})

    return {
        "date":  today,
        "usage": {
            path: {
                "used":      user_usage.get(path, 0),
                "limit":     limit,
                "remaining": max(0, limit - user_usage.get(path, 0)),
            }
            for path, limit in DAILY_LIMITS.items()
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  MIDDLEWARE
# ─────────────────────────────────────────────────────────────────────────────

class RateLimiterMiddleware(BaseHTTPMiddleware):
    """
    Middleware de rate limiting per user per zi.

    Logică per request:
      1. Verifică dacă path-ul e în DAILY_LIMITS — altfel trece transparent
      2. Extrage emailul din JWT (fără overhead de DB)
      3. Dacă nu e autentificat → trece mai departe (auth.py gestionează)
      4. Dacă a depășit limita → 429 cu detalii clare
      5. Altfel → trece mai departe, incrementează DUPĂ ce requestul reușește

    Incrementare post-request (pe non-5xx):
      · Nu penalizăm userul pentru erori Groq (503) sau erori de server (500)
      · Incrementăm pe 200, 400, 422 — succese și validări de input
      · Această abordare e corectă pentru un tool personal cu concurență mică

    Integrare în main.py (adaugă după `app = FastAPI(...)`):
        from rate_limiter import RateLimiterMiddleware
        app.add_middleware(RateLimiterMiddleware)
    """

    async def dispatch(self, request: Request, call_next):
        path  = request.url.path
        limit = DAILY_LIMITS.get(path)

        # Path nenimitat → trece transparent, zero overhead
        if limit is None:
            return await call_next(request)

        # Curăță zilele vechi (O dată per request pe endpoint limitat — cost minim)
        _purge_old_days()

        # Extrage email din JWT
        email = _extract_email_from_request(request)
        if not email:
            # Fără token valid → auth.py va returna 401
            return await call_next(request)

        today = _today()
        _ensure_user_slot(today, email, path)

        current = _store[today][email][path]

        # Limită depășită → 429 cu mesaj clar
        if current >= limit:
            msg = _LIMIT_MESSAGES.get(path, f"Limita zilnică de {limit} apeluri AI atinsă.")
            return JSONResponse(
                status_code=429,
                content={
                    "detail":    msg,
                    "limit":     limit,
                    "used":      current,
                    "remaining": 0,
                    "resets_at": "00:00 (miezul nopții)",
                    "tip":       "Limita se resetează automat la miezul nopții.",
                },
                headers={"Retry-After": "86400"},
            )

        # Trece la endpoint
        response = await call_next(request)

        # Incrementează DOAR dacă requestul a reușit (non-5xx)
        # Nu penalizăm userul pentru down-time Groq sau erori de server
        if response.status_code < 500:
            _store[today][email][path] += 1

        return response


# ─────────────────────────────────────────────────────────────────────────────
#  ROUTER OPȚIONAL — GET /rate-limits (debug + transparență pentru user)
# ─────────────────────────────────────────────────────────────────────────────

def init_rate_limit_router() -> APIRouter:
    """
    Router opțional care expune utilizarea curentă a limitelor.
    Util pentru frontend (poate afișa "X/25 mesaje rămase azi").

    Adaugă în main.py opțional:
        from rate_limiter import init_rate_limit_router
        app.include_router(init_rate_limit_router())
    """
    router = APIRouter(tags=["Rate Limits"])

    @router.get("/rate-limits")
    async def get_my_rate_limits(email: str = Depends(require_user_email)):
        """
        Returnează utilizarea curentă a limitelor zilnice pentru userul autentificat.

        Response:
            {
                "date": "2025-07-25",
                "usage": {
                    "/chat": {"used": 5, "limit": 25, "remaining": 20},
                    ...
                }
            }
        """
        return get_usage_for_email(email)

    return router