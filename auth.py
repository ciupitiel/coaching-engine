# =============================================================================
#  auth.py — Modul de Securitate
#  Noian Cristian · Coaching Engine
#  -----------------------------------------------------------------------------
#  v1.1 FIX: SECRET_KEY mutat din hardcoded → variabilă de mediu
#  Adaugă în .env:   SECRET_KEY=o-cheie-lunga-si-aleatorie-de-minim-32-caractere
#  Pe Render.com:    Environment Variables → SECRET_KEY → valoarea ta
# =============================================================================

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
import jwt

# ── Configurare ───────────────────────────────────────────────────────────────
# FIX: SECRET_KEY citit din mediu, nu hardcodat în sursă.
# Fallback la vechea valoare pentru backward compatibility (sesiunile existente rămân valide).
# ⚠️  ACȚIUNE NECESARĂ: adaugă în .env → SECRET_KEY=cheie-secreta-foarte-sigura
# ⚠️  Pe Render: Environment Variables → Add → SECRET_KEY → valoarea ta
SECRET_KEY = os.environ.get("SECRET_KEY", "cheie-secreta-foarte-sigura")

ALGORITHM            = "HS256"
TOKEN_EXPIRE_MINUTES = 60 * 24 * 7   # 7 zile — UX decent fără risc adițional

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer     = HTTPBearer(auto_error=False)


# ── Parole ────────────────────────────────────────────────────────────────────
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


# ── JWT ───────────────────────────────────────────────────────────────────────
def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire    = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ── Dependențe FastAPI ────────────────────────────────────────────────────────
def get_current_user_email(
    creds: Optional[HTTPAuthorizationCredentials] = Security(_bearer)
) -> Optional[str]:
    """
    Extrage emailul din Bearer token.
    Returnează None dacă token-ul lipsește sau e invalid.
    Folosit la /calculate (autentificare opțională — nu blochează calculul).
    """
    if creds is None:
        return None
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def require_user_email(
    creds: Optional[HTTPAuthorizationCredentials] = Security(_bearer)
) -> str:
    """
    Ca get_current_user_email, dar aruncă 401 dacă token-ul lipsește/e invalid.
    Folosit la rutele care cer autentificare obligatorie (/auth/my-sessions).
    """
    email = get_current_user_email(creds)
    if not email:
        raise HTTPException(
            status_code=401,
            detail="Sesiune expirată sau token invalid. Te rog reconectează-te."
        )
    return email