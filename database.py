# database.py
# Postgres (Railway'ning tayyor ma'lumotlar bazasi xizmati) bilan ishlash

import os
import asyncpg
from datetime import datetime, timedelta

DATABASE_URL = os.environ.get("DATABASE_URL")

_pool: asyncpg.Pool = None

async def init_db():
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
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS pages_read INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS streak_days INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_date TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_expires_at TIMESTAMP")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_daily_bonus_date TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_streak_bonus_date TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_milestone_tier INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_reminder_date TEXT")

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

        # Sirli Sandiq jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chest_opens (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                chest_type TEXT NOT NULL,
                reward_type TEXT,
                reward_amount INTEGER,
                opened_date TEXT
            )
        """)

# ---------- FOYDALANUVCHILAR ----------

async def get_user(user_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

async def create_user_if_missing(user_id: int, username: str, referred_by: int = None) -> bool:
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

# ---------- STATISTIKA ----------

async def increment_pages_read(user_id: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET pages_read = pages_read + 1 WHERE user_id = $1 RETURNING pages_read",
            user_id,
        )
        return row["pages_read"] if row else 0

async def update_streak(user_id: int) -> int:
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
            return streak

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

# ---------- STREAK BONUSI ----------

async def try_grant_streak_bonus(user_id: int, amount: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_streak_bonus_date FROM users WHERE user_id = $1", user_id
        )
        if row and row["last_streak_bonus_date"] == today:
            return 0

        await conn.execute(
            "UPDATE users SET coins = coins + $1, last_streak_bonus_date = $2 WHERE user_id = $3",
            amount, today, user_id,
        )
        return amount

# ---------- PREMIUM ----------

async def check_premium_status(user_id: int) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_premium, premium_expires_at FROM users WHERE user_id = $1", user_id
        )
        if not row or not row["is_premium"]:
            return False

        if row["premium_expires_at"] and row["premium_expires_at"] <= datetime.now():
            await conn.execute(
                "UPDATE users SET is_premium = FALSE WHERE user_id = $1", user_id
            )
            return False

        return True

async def activate_premium(user_id: int, days: int = 7) -> datetime:
    now = datetime.now()
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_premium, premium_expires_at FROM users WHERE user_id = $1", user_id
        )
        current_expiry = row["premium_expires_at"] if row else None

        if row and row["is_premium"] and current_expiry and current_expiry > now:
            new_expiry = current_expiry + timedelta(days=days)
        else:
            new_expiry = now + timedelta(days=days)

        await conn.execute(
            "UPDATE users SET is_premium = TRUE, premium_expires_at = $1 WHERE user_id = $2",
            new_expiry, user_id,
        )
    return new_expiry

async def try_grant_daily_bonus(user_id: int, amount: int) -> int:
    is_premium = await check_premium_status(user_id)
    if not is_premium:
        return 0

    today = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_daily_bonus_date FROM users WHERE user_id = $1", user_id
        )
        if row and row["last_daily_bonus_date"] == today:
            return 0

        await conn.execute(
            "UPDATE users SET coins = coins + $1, last_daily_bonus_date = $2 WHERE user_id = $3",
            amount, today, user_id,
        )
        return amount

# ---------- REFERRAL ----------

async def get_referral_count(user_id: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM users WHERE referred_by = $1", user_id
        )
        return row["cnt"] if row else 0

async def get_referral_milestone_tier(user_id: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT referral_milestone_tier FROM users WHERE user_id = $1", user_id
        )
        return row["referral_milestone_tier"] if row and row["referral_milestone_tier"] else 0

async def set_referral_milestone_tier(user_id: int, tier: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET referral_milestone_tier = $1 WHERE user_id = $2", tier, user_id
        )

# ---------- REYTING ----------

async def get_coins_leaderboard(limit: int = 10):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT user_id, username, coins FROM users ORDER BY coins DESC LIMIT $1", limit
        )

async def get_referral_leaderboard(limit: int = 10):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT u.user_id, u.username, COUNT(r.user_id) as cnt
            FROM users u
            JOIN users r ON r.referred_by = u.user_id
            GROUP BY u.user_id, u.username
            ORDER BY cnt DESC
            LIMIT $1
            """,
            limit,
        )

# ---------- ESLATMA ----------

async def get_users_for_reminder(inactive_days: int = 1):
    cutoff = (datetime.now() - timedelta(days=inactive_days)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT user_id FROM users
            WHERE (last_active_date IS NULL OR last_active_date <= $1)
              AND (last_reminder_date IS NULL OR last_reminder_date != $2)
            """,
            cutoff, today,
        )

async def mark_reminder_sent(user_id: int):
    today = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET last_reminder_date = $1 WHERE user_id = $2", today, user_id
        )

# ---------- DO'KON ----------

async def is_item_owned(user_id: int, item_id: str) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM purchases WHERE user_id = $1 AND item_id = $2", user_id, item_id
        )
        return row is not None

async def add_purchase(user_id: int, item_id: str, item_name: str, item_emoji: str, item_type: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO purchases (user_id, item_id, item_name, item_emoji, item_type, purchased_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            user_id, item_id, item_name, item_emoji, item_type,
            datetime.now().strftime("%Y-%m-%d %H:%M")
        )

async def get_user_purchases(user_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM purchases WHERE user_id = $1 ORDER BY id DESC", user_id
        )

# ---------- SIRLI SANDIQ ----------

async def get_chest_opens_today(user_id: int, chest_type: str) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) as cnt FROM chest_opens 
            WHERE user_id = $1 AND chest_type = $2 AND opened_date = $3
            """,
            user_id, chest_type, today
        )
        return row["cnt"] if row else 0

async def record_chest_open(user_id: int, chest_type: str, reward_type: str, reward_amount: int):
    today = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO chest_opens (user_id, chest_type, reward_type, reward_amount, opened_date)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id, chest_type, reward_type, reward_amount, today
    )
