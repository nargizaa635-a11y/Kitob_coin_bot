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
        # Statistika uchun yangi ustunlar (eski jadvalga xavfsiz qo'shiladi)
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS pages_read INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS streak_days INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_date TEXT")

        # Do'kondan qilingan xaridlar - endi doimiy saqlanadi (localStorage emas)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                item_id TEXT NOT NULL,
                item_name TEXT,
                item_emoji TEXT,
                item_type TEXT,
                purchased_at TEXT
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


# ---------- STATISTIKA (Sahifam uchun) ----------

async def increment_pages_read(user_id: int) -> int:
    """Kitob sahifasi o'qilganda chaqiriladi, jami o'qilgan sahifalar sonini qaytaradi."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET pages_read = pages_read + 1 WHERE user_id = $1 RETURNING pages_read",
            user_id,
        )
        return row["pages_read"] if row else 0


async def update_streak(user_id: int) -> int:
    """Foydalanuvchi ilovani ochganda kunlik seriyani (streak) yangilaydi."""
    today = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT last_active_date, streak_days FROM users WHERE user_id = $1", user_id
        )
        if not user:
            return 0

        last_date = user["last_active_date"]
        streak = user["streak_days"] or 0

        if last_date == today:
            return streak  # bugun allaqachon hisoblangan

        if last_date:
            try:
                last_dt = datetime.strptime(last_date, "%Y-%m-%d")
                today_dt = datetime.strptime(today, "%Y-%m-%d")
                diff_days = (today_dt - last_dt).days
            except Exception:
                diff_days = None
            streak = streak + 1 if diff_days == 1 else 1
        else:
            streak = 1

        await conn.execute(
            "UPDATE users SET streak_days = $1, last_active_date = $2 WHERE user_id = $3",
            streak, today, user_id,
        )
        return streak


# ---------- DO'KON XARIDLARI ----------

async def is_item_owned(user_id: int, item_id: str) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM purchases WHERE user_id = $1 AND item_id = $2", user_id, item_id
        )
        return row is not None


async def add_purchase(user_id: int, item_id: str, item_name: str, item_emoji: str, item_type: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO purchases (user_id, item_id, item_name, item_emoji, item_type, purchased_at) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            user_id, item_id, item_name, item_emoji, item_type,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


async def get_purchases(user_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM purchases WHERE user_id = $1 ORDER BY purchased_at DESC", user_id
        )


async def count_purchases_by_type(user_id: int, item_type: str) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM purchases WHERE user_id = $1 AND item_type = $2",
            user_id, item_type,
        )
        return row["cnt"] if row else 0
