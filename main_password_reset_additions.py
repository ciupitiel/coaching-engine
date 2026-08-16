# =============================================================================
#  main_password_reset_additions.py — Password Reset · Router
#  Noian Cristian · Bazat pe inteligență artificială
#  -----------------------------------------------------------------------------
#  Modul NOU. Adaugă în main.py exact 4 linii:
#
#  ① La importuri (după `from main_p6_additions import init_p6_router`):
#       from main_password_reset_additions import init_password_reset_router
#       from database_password_reset import init_db_password_reset
#
#  ② În lifespan(), DUPĂ `await init_db_settings_e5()`:
#       await init_db_password_reset()
#
#  ③ La routers, DUPĂ `app.include_router(init_streak_router())`:
#       app.include_router(init_password_reset_router())
#
#  Endpoint-uri expuse:
#    POST /auth/forgot-password  → primește email, trimite link cu token
#    POST /auth/reset-password   → validează token, setează parola nouă
#    GET  /auth/validate-token   → verifică dacă token e valid (folosit de UI)
#
#  Securitate implementată:
#    · Anti-enumeration: /forgot-password returnează același mesaj indiferent
#      dacă emailul există sau nu în DB. Atacatorul nu poate afla ce emailuri
#      sunt înregistrate prin această rută.
#    · Token UUID v4 cu expirare 15 minute.
#    · Un token = un singur uz (marcat `used=True` după resetare).
#    · Token-uri anterioare ale aceluiași user sunt invalidate la fiecare request.
#    · Parola nouă: minim 8 caractere (validare Pydantic + validare JS).
# =============================================================================

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from auth import get_password_hash
from database import get_pool
from database_password_reset import (
    create_reset_token,
    mark_token_used,
    validate_reset_token,
)
from email_service import send_password_reset_email


# ─────────────────────────────────────────────────────────────────────────────
#  MODELE PYDANTIC
# ─────────────────────────────────────────────────────────────────────────────

class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)


class ResetPasswordRequest(BaseModel):
    token:        str = Field(..., min_length=36, max_length=36,
                              description="UUID token din emailul de resetare")
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v.strip()) < 8:
            raise ValueError("Parola trebuie să aibă cel puțin 8 caractere.")
        return v


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITAR INTERN
# ─────────────────────────────────────────────────────────────────────────────

async def _user_exists(email: str) -> bool:
    """Verifică dacă emailul există în tabelul users. Zero informație expusă extern."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM users WHERE LOWER(email) = LOWER($1)",
            email,
        )
    return row is not None


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY ROUTER — același pattern ca P4, P5, P6
# ─────────────────────────────────────────────────────────────────────────────

def init_password_reset_router() -> APIRouter:
    """
    Creează și returnează router-ul Password Reset.
    Apelat O SINGURĂ DATĂ la pornirea serverului din main.py:
        app.include_router(init_password_reset_router())
    """
    router = APIRouter(tags=["Auth · Password Reset"])

    # ── POST /auth/forgot-password ────────────────────────────────────────────
    @router.post("/auth/forgot-password")
    async def forgot_password(req: ForgotPasswordRequest):
        """
        Inițiază fluxul de resetare parolă.

        Flux:
          1. Validează formatul emailului (Pydantic)
          2. Verifică dacă emailul există în DB (intern, NEDIVULGAT extern)
          3. Dacă există: generează token UUID, îl salvează, trimite emailul
          4. Returnează ACELAȘI mesaj indiferent de existența emailului
             → Anti-enumeration: atacatorul nu poate scaneze ce emailuri sunt înregistrate

        Timing attack: generate_token() este apelat DOAR când userul există.
        Asta poate crea o diferență de timp măsurabilă. La scara noastră (personal
        coaching tool, nu bancă), aceasta este acceptabilă și nu necesită fake delay.
        """
        email       = req.email.strip().lower()
        user_exists = await _user_exists(email)

        if user_exists:
            token = await create_reset_token(email)
            sent  = await send_password_reset_email(email, token)

            if not sent:
                # Logăm intern — emailul poate eșua (Resend down, rate limit)
                # dar nu expunem eroarea clientului (ar divulga că emailul există)
                print(f"⚠️  Reset email failed pentru {email} (token salvat în DB)")

        # Același răspuns indiferent de existența emailului → anti-enumeration
        return {
            "ok": True,
            "message": (
                "Dacă adresa de email este înregistrată, "
                "vei primi un email cu instrucțiuni în câteva minute. "
                "Verifică și folderul Spam."
            ),
        }

    # ── POST /auth/reset-password ─────────────────────────────────────────────
    @router.post("/auth/reset-password")
    async def reset_password(req: ResetPasswordRequest):
        """
        Resetează parola cu un token valid.

        Flux:
          1. Validează token (există, nefolosit, neexpirat) → returnează emailul
          2. Hashează parola nouă cu bcrypt
          3. Actualizează password_hash în tabelul users
          4. Marchează token-ul ca folosit (un token = un singur uz)
          5. Returnează confirmare

        Dacă token-ul e invalid/expirat → HTTP 400 cu mesaj clar.
        """
        email = await validate_reset_token(req.token)

        if not email:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Link de resetare invalid sau expirat. "
                    "Solicită un link nou din pagina de conectare."
                ),
            )

        # Actualizăm parola în DB
        new_hash = get_password_hash(req.new_password)
        async with get_pool().acquire() as conn:
            await conn.execute(
                "UPDATE users SET password_hash = $1 WHERE LOWER(email) = LOWER($2)",
                new_hash, email,
            )

        # Invalidăm token-ul imediat după folosire
        await mark_token_used(req.token)

        print(f"✅  Parolă resetată pentru {email}")

        return {
            "ok":      True,
            "message": "Parola a fost resetată cu succes. Te poți conecta acum.",
        }

    # ── GET /auth/validate-token ──────────────────────────────────────────────
    @router.get("/auth/validate-token")
    async def validate_token_endpoint(token: str):
        """
        Verifică rapid dacă un token de resetare e valid.
        Folosit de JS din index.html la încărcarea paginii cu ?reset_token=...
        → Afișează formularul DOAR dacă token-ul e valid.
        → Nu returnează emailul asociat (informație sensibilă inutilă pentru frontend).

        Returnează: {valid: bool, message: str}
        """
        email = await validate_reset_token(token)
        return {
            "valid":   email is not None,
            "message": "Token valid." if email else "Token invalid sau expirat.",
        }

    return router