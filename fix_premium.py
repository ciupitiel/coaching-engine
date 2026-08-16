import asyncio
import os

# Încarcă .env manual
env_path = '/Users/noiancristian/Desktop/coaching_engine/.env'
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

from database import init_pool, get_pool

async def make_premium():
    await init_pool()
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE users
            SET is_premium = TRUE,
                subscription_status = 'active',
                premium_until = NOW() + INTERVAL '1 year'
            WHERE email = 'noiancristian234@gmail.com'
        """)
        print("✅ Premium activat! Rows updated:", result)
    await pool.close()

asyncio.run(make_premium())