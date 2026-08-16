"""
database_share.py
Token-uri de partajare publică a progresului.

Tabel: share_tokens
  user_email : TEXT UNIQUE  → un singur token per user
  token      : TEXT UNIQUE  → URL-safe, 12 bytes (~96 biți entropie)
  created_at : TEXT

POST /share/generate  → get_or_create_share_token()
GET  /share/me        → get_share_token_for_user()
GET  /share/{token}   → get_email_by_share_token()
DEL  /share/revoke    → revoke_share_token()
"""

import datetime
import secrets
from database import get_pool


async def init_db_share() -> None:
    """Creează tabelul share_tokens. Idempotent."""
    async with get_pool().acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS share_tokens (
                id         SERIAL PRIMARY KEY,
                user_email TEXT   NOT NULL UNIQUE,
                token      TEXT   NOT NULL UNIQUE,
                created_at TEXT   NOT NULL
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_share_token ON share_tokens(token)"
        )
    print("✅  share_tokens: OK")


async def get_or_create_share_token(email: str) -> str:
    """
    Returnează token-ul existent sau îl creează pe cel nou.
    Idempotent: apeluri repetate returnează același token.
    """
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    async with get_pool().acquire() as conn:
        # Upsert: dacă există deja, returnează cel vechi
        row = await conn.fetchrow(
            """
            INSERT INTO share_tokens (user_email, token, created_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_email) DO UPDATE SET user_email = EXCLUDED.user_email
            RETURNING token
            """,
            email.lower(),
            secrets.token_urlsafe(12),
            now,
        )
    return row["token"]


async def get_share_token_for_user(email: str) -> str | None:
    """Returnează token-ul existent sau None dacă n-a fost generat."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT token FROM share_tokens WHERE LOWER(user_email) = LOWER($1)",
            email,
        )
    return row["token"] if row else None


async def get_email_by_share_token(token: str) -> str | None:
    """Returnează emailul asociat token-ului, sau None dacă e invalid."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_email FROM share_tokens WHERE token = $1",
            token,
        )
    return row["user_email"] if row else None


async def revoke_share_token(email: str) -> bool:
    """Șterge token-ul userului. Returnează True dacă a existat."""
    async with get_pool().acquire() as conn:
        result = await conn.execute(
            "DELETE FROM share_tokens WHERE LOWER(user_email) = LOWER($1)",
            email,
        )
    return result != "DELETE 0"