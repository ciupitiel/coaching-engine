# =============================================================================
#  database_email_verification.py — Email Verification · DB Layer
#  Noian Cristian · Bazat pe inteligență artificială
#  -----------------------------------------------------------------------------
#  Modul NOU. Nu modifică niciun fișier existent.
#
#  Ce face:
#    · Creează tabelul email_verifications
#    · Migrează tabelul users cu coloana is_verified
#      DEFAULT TRUE → userii existenți rămân activi (zero downtime)
#      Userii noi: mark_user_unverified() îi setează explicit FALSE
#    · Token-urile nu au expirare — userul poate verifica oricând
#      (poate cere un link nou dacă l-a pierdut)
#
#  Inserții în main.py (6 linii, detalii în main_email_verification_additions.py):
#    ① Import: from database_email_verification import (
#                  init_db_email_verification, create_verification_token,
#                  consume_verification_token, mark_user_unverified,
#                  is_user_verified
#              )
#    ② lifespan(): await init_db_email_verification()
#    ③ /auth/signup: după create_user(), apelează mark_user_unverified + token + email
#    ④ /auth/login: verifică is_user_verified înainte de token
#    ⑤ Include router: app.include_router(init_email_verification_router())
#
#  Funcții publice:
#    init_db_email_verification()      → idempotent, din lifespan()
#    create_verification_token(email)  → UUID str, invalidează token-uri vechi
#    consume_verification_token(token) → email str | None, marchează + verifică user
#    mark_user_unverified(email)       → setează is_verified=FALSE pentru useri noi
#    is_user_verified(email)           → bool
#    user_exists_and_unverified(email) → bool (pentru resend endpoint)
# =============================================================================

import uuid
import datetime
from database import get_pool


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA + MIGRARE
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS email_verifications (
    id          SERIAL PRIMARY KEY,
    user_email  TEXT    NOT NULL,
    token       TEXT    NOT NULL UNIQUE,
    used        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TEXT    NOT NULL
)
"""

# DEFAULT TRUE → userii existenți (creați înainte de această migrare) sunt
# automat verificați. Userii noi sunt setați manual ca FALSE prin mark_user_unverified().
_MIGRATION_ADD_IS_VERIFIED = """
ALTER TABLE users
ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT TRUE
"""


# ─────────────────────────────────────────────────────────────────────────────
#  INIȚIALIZARE
# ─────────────────────────────────────────────────────────────────────────────

async def init_db_email_verification() -> None:
    """
    Creează tabelul email_verifications și migrează users.is_verified.
    Idempotent — sigur de rulat la fiecare startup.
    Apelat din lifespan() în main.py, după init_db_password_reset().
    """
    async with get_pool().acquire() as conn:
        await conn.execute(_SCHEMA_TABLE)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ev_token ON email_verifications(token)"
        )
        await conn.execute(_MIGRATION_ADD_IS_VERIFIED)
    print("✅  email_verifications: OK | users.is_verified: migrat")


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚII PUBLICE
# ─────────────────────────────────────────────────────────────────────────────

async def mark_user_unverified(email: str) -> None:
    """
    Setează is_verified=FALSE pentru un user proaspăt creat.

    De ce există această funcție:
      Migrarea setează DEFAULT TRUE pentru backward compat.
      Dacă am modifica create_user() din database.py să insereze FALSE,
      am introduce o dependență în modulul de bază. Preferăm să păstrăm
      database.py intact și să apelăm mark_user_unverified() imediat
      după create_user() în endpoint-ul /auth/signup.
    """
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_verified = FALSE WHERE LOWER(email) = LOWER($1)",
            email,
        )


async def create_verification_token(email: str) -> str:
    """
    Generează un UUID token și îl salvează în DB.

    Securitate:
      · Invalidează TOATE token-urile anterioare ale aceluiași email
        → un singur token activ per user în orice moment
      · UUID v4 = 2^122 spațiu de căutare → imposibil de ghicit
      · Fără expirare — userul poate verifica oricând; dacă pierde emailul
        poate cere resend prin POST /auth/resend-verification

    Returns:
        Token UUID string (36 caractere)
    """
    token = str(uuid.uuid4())
    now   = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()

    async with get_pool().acquire() as conn:
        # Invalidăm token-urile active anterioare
        await conn.execute(
            """
            UPDATE email_verifications
            SET used = TRUE
            WHERE LOWER(user_email) = LOWER($1) AND used = FALSE
            """,
            email,
        )
        # Inserăm token-ul nou
        await conn.execute(
            """
            INSERT INTO email_verifications (user_email, token, used, created_at)
            VALUES ($1, $2, FALSE, $3)
            """,
            email.lower(), token, now,
        )

    return token


async def consume_verification_token(token: str) -> str | None:
    """
    Validează și consumă un token de verificare.

    Dacă token-ul e valid (există + nefolosit):
      1. Marchează token-ul ca folosit (single-use)
      2. Setează is_verified=TRUE pe tabelul users
      3. Returnează emailul utilizatorului

    Dacă e invalid (negăsit sau deja folosit): returnează None.

    Tot într-o singură conexiune pentru atomicitate — nu există fereastră
    în care token-ul e marcat ca folosit dar userul e încă neverificat.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_email, used FROM email_verifications WHERE token = $1",
            token,
        )

        if not row or row["used"]:
            return None  # Token necunoscut sau deja folosit

        email = row["user_email"]

        # Marchează token ca folosit
        await conn.execute(
            "UPDATE email_verifications SET used = TRUE WHERE token = $1",
            token,
        )
        # Marchează userul ca verificat
        await conn.execute(
            "UPDATE users SET is_verified = TRUE WHERE LOWER(email) = LOWER($1)",
            email,
        )

    return email


async def is_user_verified(email: str) -> bool:
    """
    Verifică dacă userul și-a confirmat emailul.

    Fallback TRUE dacă coloana lipsește sau row-ul nu există —
    previne lock-out-ul accidental în caz de eroare de migrare.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_verified FROM users WHERE LOWER(email) = LOWER($1)",
            email,
        )
    if not row:
        return False
    # get() cu default True: dacă coloana lipsește din row (pre-migrare), nu blocăm
    return bool(row.get("is_verified", True))


async def user_exists_and_unverified(email: str) -> bool:
    """
    Verifică că emailul există în DB și nu este încă verificat.
    Folosit în endpoint-ul POST /auth/resend-verification pentru a valida
    dacă are sens să trimitem un nou email.
    Returnează False și dacă emailul nu există (anti-enumeration la nivel de logică).
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_verified FROM users WHERE LOWER(email) = LOWER($1)",
            email,
        )
    if not row:
        return False
    return not bool(row.get("is_verified", True))