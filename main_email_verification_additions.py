from fastapi import APIRouter
from pydantic import BaseModel, Field
from email_service import send_verification_email
from database_email_verification import (
    consume_verification_token,
    create_verification_token,
    user_exists_and_unverified,
)


# ─────────────────────────────────────────────────────────────────────────────
#  MODELE PYDANTIC
# ─────────────────────────────────────────────────────────────────────────────

class ResendVerificationRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY ROUTER
# ─────────────────────────────────────────────────────────────────────────────

def init_email_verification_router() -> APIRouter:
    """
    Creează și returnează router-ul pentru verificarea emailului.
    Apelat O SINGURĂ DATĂ la pornirea serverului din main.py:
        app.include_router(init_email_verification_router())
    """
    router = APIRouter(tags=["Auth · Email Verification"])

    # ── GET /auth/verify-email?token=xxx ─────────────────────────────────────
    @router.get("/auth/verify-email")
    async def verify_email(token: str):
        """
        Validează un token de verificare email.

        Flux:
          1. Caută token-ul în DB (exist + nefolosit)
          2. Marchează token-ul ca folosit (single-use)
          3. Setează is_verified=TRUE pe tabelul users
          4. Returnează confirmare

        Apelat din JS la detectarea ?verify_token= în URL.
        Nu redirect — JS-ul gestionează UX după confirmare.

        Token invalid/deja folosit → 200 cu ok:false (nu 400/404 deoarece
        JS-ul din browser accesează direct acest endpoint; un 4xx ar fi
        tratat ca eroare de rețea în unele browsere vechi).
        """
        email = await consume_verification_token(token)

        if not email:
            return {
                "ok":      False,
                "message": (
                    "Link de confirmare invalid sau deja folosit. "
                    "Solicită un link nou din pagina de conectare."
                ),
            }

        print(f"✅  Email verificat: {email}")

        # Completează referral pending dacă există
        try:
            from database_referral import complete_referral_if_exists
            await complete_referral_if_exists(email)
        except Exception as _e:
            print(f"⚠️  Referral hook error: {_e}")

        return {
            "ok":      True,
            "message": "Email confirmat cu succes! Te poți conecta acum.",
            "email":   email,
        }

    # ── POST /auth/resend-verification ────────────────────────────────────────
    @router.post("/auth/resend-verification")
    async def resend_verification(req: ResendVerificationRequest):
        """
        Retrimite emailul de verificare.

        Anti-enumeration: returnează ACELAȘI mesaj indiferent dacă emailul
        există sau nu, și indiferent dacă e deja verificat sau nu.
        Atacatorul nu poate folosi acest endpoint pentru a afla ce emailuri
        sunt înregistrate și neconfirmate în baza ta de date.

        Flux intern (invizibil pentru client):
          1. Verifică că emailul există + nu e verificat
          2. Dacă da: generează token nou, trimite email
          3. Returnează același mesaj în ambele cazuri
        """
        email = req.email.strip().lower()
        needs_resend = await user_exists_and_unverified(email)

        if needs_resend:
            token = await create_verification_token(email)
            sent  = await send_verification_email(email, token)
            if not sent:
                print(f"⚠️  Resend verification email failed pentru {email}")

        # Același răspuns întotdeauna → anti-enumeration
        return {
            "ok": True,
            "message": (
                "Dacă adresa de email este înregistrată și neconfirmată, "
                "vei primi un nou link de confirmare în câteva minute. "
                "Verifică și folderul Spam."
            ),
        }

    return router