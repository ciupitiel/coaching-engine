# =============================================================================
#  premium_guard.py — require_premium Dependency FastAPI
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  Fișier NOU, standalone. Nu modifică auth.py (zero risc de regresie).
#
#  Cum funcționează:
#    · require_premium internalizează require_user_email
#    · Dacă userul e autentificat dar nu e premium → 402 Payment Required
#    · Frontend-ul detectează 402 și afișează modal "Upgrade Premium"
#    · Dacă e premium → returnează emailul, endpoint-ul continuă normal
#
#  Utilizare în orice router existent:
#    # Înainte:
#    from auth import require_user_email
#    email: str = Depends(require_user_email)
#
#    # După:
#    from premium_guard import require_premium
#    email: str = Depends(require_premium)
#
#  require_premium este un superset al require_user_email:
#    require_premium = require_user_email + verificare is_premium din DB
#    Un request care trece require_premium garantează:
#      ① JWT valid (autentificat)
#      ② is_premium = TRUE în DB (abonat activ)
# =============================================================================

from fastapi import Depends, HTTPException
from auth import require_user_email


async def require_premium(email: str = Depends(require_user_email)) -> str:
    """
    FastAPI dependency — verifică că userul autentificat are abonament Premium activ.

    Args:
        email : injectat automat de require_user_email (JWT valid)

    Returns:
        emailul userului (string) — identic cu require_user_email

    Raises:
        401 : token lipsă / invalid (de la require_user_email)
        402 : user autentificat, dar fără abonament Premium

    Response 402 — structura JSON returnată frontend-ului:
        {
            "detail": {
                "error":       "premium_required",
                "message":     "...",
                "upgrade_url": "/stripe/checkout",
                "free_tier":   "Calculator · Food Logger · Chat · Streak",
                "premium_tier": "Meal Plans RAG · PDF Reports · Analytics · Adaptive AI"
            }
        }

    De ce 402 și nu 403?
        · 403 Forbidden = autentificat, dar fără permisiune (ex: nu ești admin)
        · 402 Payment Required = autentificat, dar plata necesară pentru acces
        · Frontend poate distinge ușor: 403 → "acces interzis" | 402 → "upgrade modal"
    """
    # Import lazy → evitare circulare la startup
    # (premium_guard → database_stripe → database → pool)
    from database_stripe import get_user_subscription

    sub = await get_user_subscription(email)

# Verifică expirarea trial-ului (useri cu premium_until dar fără sub Stripe activă)
    if bool(sub.get("is_premium", False)) and sub.get("subscription_status") == "trial":
        import datetime as _dt
        pu = sub.get("premium_until", "")
        if pu:
            try:
                if _dt.datetime.now() > _dt.datetime.fromisoformat(pu[:19]):
                    from database_stripe import update_subscription as _upd
                    await _upd(email=email, is_premium=False, subscription_status="expired")
                    sub["is_premium"] = False
            except ValueError:
                pass

    if not bool(sub.get("is_premium", False)):
        status = sub.get("subscription_status", "none")

        # Mesaj diferit dacă a fost abonat și a expirat vs niciodată abonat
        if status in ("past_due", "cancelled", "unpaid"):
            msg = (
                "Abonamentul tău Premium a expirat sau plata a eșuat. "
                "Reactivează din secțiunea Premium."
            )
        else:
            msg = (
                "Această funcție necesită abonament Premium (5–9€/lună). "
                "Calculatorul, food logger-ul și chat-ul rămân gratuite."
            )

        raise HTTPException(
            status_code=402,
            detail={
                "error":        "premium_required",
                "message":      msg,
                "upgrade_url":  "/stripe/checkout",
                "status":       status,
                "free_tier":    "Calculator TDEE · Food Logger · Barcode · Chat 25msg · Streak",
                "premium_tier": "Meal Plans RAG · Rapoarte PDF · Analytics 4 săpt. · Adaptive AI",
            },
        )

    return email