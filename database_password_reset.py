# =============================================================================
#  database_password_reset.py — Password Reset · DB Layer
#  Noian Cristian · Bazat pe inteligență artificială
#  -----------------------------------------------------------------------------
#  Modul NOU. Nu modifică niciun fișier existent.
#
#  Schema: password_reset_tokens
#    · token      → UUID v4 unic trimis prin email (imposibil de ghicit)
#    · expires_at → 15 minute de la creare (UTC ISO string)
#    · used       → boolean, True după resetare (un token = un singur uz)
#
#  Funcții publice:
#    init_db_password_reset()    → creează tabelul + index; apelat din lifespan()
#    create_reset_token(email)   → invalidează token-uri vechi, generează UUID nou
#    validate_reset_token(token) → returnează email dacă valid, None altfel
#    mark_token_used(token)      → marchează token ca folosit după resetare
# =============================================================================

import uuid
import datetime
from database import get_pool


_SCHEMA = """
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          SERIAL PRIMARY KEY,
    user_email  TEXT    NOT NULL,
    token       TEXT    NOT NULL UNIQUE,
    expires_at  TEXT    NOT NULL,
    used        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TEXT    NOT NULL
)
"""

_TOKEN_EXPIRY_MINUTES = 15


async def init_db_password_reset() -> None:
    """
    Creează tabelul password_reset_tokens dacă nu există. Idempotent.
    Apelat din lifespan() în main.py, după await init_db_settings_e5().
    """
    async with get_pool().acquire() as conn:
        await conn.execute(_SCHEMA)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prt_token ON password_reset_tokens(token)"
        )
    print("✅  password_reset_tokens: OK")


async def create_reset_token(email: str) -> str:
    """
    Generează un token UUID unic și îl salvează în DB.

    Securitate:
      · Invalidează TOATE token-urile anterioare ale aceluiași email.
        → Dacă userul solicită de două ori, al doilea token îl anulează pe primul.
      · UUID v4 = 2^122 spațiu de căutare → imposibil de ghicit prin brute force.
      · Expiră în _TOKEN_EXPIRY_MINUTES minute (15 min implicit).

    Returns:
        Token UUID string (36 caractere, format xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
    """
    token      = str(uuid.uuid4())
    now        = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    expires_at = (now + datetime.timedelta(minutes=_TOKEN_EXPIRY_MINUTES)).isoformat()
    created_at = now.isoformat()

    async with get_pool().acquire() as conn:
        # Invalidăm token-urile active anterioare ale aceluiași user
        await conn.execute(
            """
            UPDATE password_reset_tokens
            SET used = TRUE
            WHERE LOWER(user_email) = LOWER($1) AND used = FALSE
            """,
            email,
        )
        # Inserăm token-ul nou
        await conn.execute(
            """
            INSERT INTO password_reset_tokens
                (user_email, token, expires_at, used, created_at)
            VALUES ($1, $2, $3, FALSE, $4)
            """,
            email.lower(), token, expires_at, created_at,
        )

    return token


async def validate_reset_token(token: str) -> str | None:
    """
    Validează un token de resetare.

    Verificări (în ordine):
      1. Token-ul există în DB
      2. Nu a fost folosit anterior
      3. Nu a expirat (expires_at > now UTC)

    Returns:
        Email-ul asociat dacă token-ul e valid, altfel None.
        Nu aruncă excepții — orice caz invalid returnează None.
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT user_email, expires_at, used
            FROM password_reset_tokens
            WHERE token = $1
            """,
            token,
        )

    if not row:
        return None  # Token necunoscut

    if row["used"]:
        return None  # Token deja folosit

    expires_at = datetime.datetime.fromisoformat(row["expires_at"])
    now        = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)  

    if now > expires_at:
        return None  # Token expirat

    return row["user_email"]


async def mark_token_used(token: str) -> None:
    """
    Marchează token-ul ca folosit imediat după resetarea parolei.
    Previne reutilizarea aceluiași link de email.
    """
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE password_reset_tokens SET used = TRUE WHERE token = $1",
            token,
        )