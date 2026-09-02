# database.py
# Postgres (Railway'ning tayyor ma'lumotlar bazasi xizmati) bilan ishlash
# Volume shart emas - Postgres ma'lumotlarni o'zi doimiy saqlaydi

import os
import asyncpg
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL")

_pool: asyncpg.Pool = None


async def init_db():
    """Ma'lumotlar bazasiga ulanib, kerakli jadvallarni yaratadi."""
    global _pool
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL topilmadi! Railway'da 'web' xizmatiga DATABASE_URL "
            "o'zgaruvchisini qo'shganingizni tekshiring."
        )
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                coins INTEGER DEFAULT 0,
                referred_by BIGINT,
                referral_rewarded BOOLEAN DEFAULT FALSE,
                joined_date TEXT
            )
        """)


# ---------- FOYDALANUVCHILAR ----------

async def get_user(user_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)


async def create_user_if_missing(user_id: int, username: str, referred_by: int = None) -> bool:
    """True qaytarsa - bu yangi foydalanuvchi."""
    async with _pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT 1 FROM users WHERE user_id = $1", user_id)
        if existing:
            return False
        await conn.execute(
            "INSERT INTO users (user_id, username, coins, referred_by, joined_date) "
            "VALUES ($1, $2, 0, $3, $4)",
            user_id, username, referred_by, datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        return True


async def add_coins(user_id: int, amount: int) -> int:
    """Koin qo'shadi (yoki manfiy son bilan ayiradi) va yangi balansni qaytaradi."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET coins = coins + $1 WHERE user_id = $2 RETURNING coins",
            amount, user_id,
        )
        return row["coins"] if row else 0


async def mark_referral_rewarded(user_id: int):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE users SET referral_rewarded = TRUE WHERE user_id = $1", user_id)


async def get_all_users():
    async with _pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM users")

