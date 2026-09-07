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
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_free_daily_bonus_date TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quiz_correct_total INTEGER DEFAULT 0")
        # Profil kengaytmasi: test statistikasi, viloyat/telefon
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_quiz_attempts INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_quiz_questions INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quiz_score_total INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS region TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_path TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_reward_date TEXT")

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

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                task_type TEXT NOT NULL,
                goal INTEGER NOT NULL,
                reward INTEGER NOT NULL,
                active BOOLEAN DEFAULT TRUE
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_task_progress (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                task_id INTEGER NOT NULL REFERENCES tasks(id),
                claimed_at TEXT NOT NULL,
                UNIQUE (user_id, task_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reading_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                book_title TEXT,
                page_number INTEGER,
                coins_earned INTEGER,
                read_at TEXT
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS weekly_draws (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                goal INTEGER NOT NULL,
                reward_text TEXT NOT NULL,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                active BOOLEAN DEFAULT TRUE
            )
        """)
        await conn.execute("ALTER TABLE weekly_draws ADD COLUMN IF NOT EXISTS image_path TEXT")
        await conn.execute("ALTER TABLE weekly_draws ADD COLUMN IF NOT EXISTS required_channels TEXT")
        await conn.execute("ALTER TABLE weekly_draws ADD COLUMN IF NOT EXISTS prizes TEXT")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS winners (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                week_label TEXT,
                referral_count INTEGER,
                prize TEXT,
                won_date TEXT
            )
        """)

        # ---------- KITOBLAR (PDF) ----------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                author TEXT,
                genre TEXT,
                cover_path TEXT,
                page_count INTEGER NOT NULL DEFAULT 0,
                required_referrals INTEGER NOT NULL DEFAULT 0,
                required_coins INTEGER NOT NULL DEFAULT 0,
                added_at TEXT,
                active BOOLEAN DEFAULT TRUE
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS book_pages (
                id SERIAL PRIMARY KEY,
                book_id INTEGER NOT NULL REFERENCES books(id),
                page_number INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                UNIQUE (book_id, page_number)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_book_unlocks (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                book_id INTEGER NOT NULL REFERENCES books(id),
                unlocked_at TEXT NOT NULL,
                UNIQUE (user_id, book_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_book_progress (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                book_id INTEGER NOT NULL REFERENCES books(id),
                current_page INTEGER NOT NULL DEFAULT 0,
                UNIQUE (user_id, book_id)
            )
        """)

        # Anti-farming: bitta (user, book, page) uchun koin faqat BIR MARTA beriladi
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_page_reads (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                book_id INTEGER NOT NULL REFERENCES books(id),
                page_number INTEGER NOT NULL,
                read_at TEXT NOT NULL,
                UNIQUE (user_id, book_id, page_number)
            )
        """)

        await _seed_default_tasks(conn)


async def _seed_default_tasks(conn):
    existing = await conn.fetchrow("SELECT 1 FROM tasks LIMIT 1")
    if existing:
        return
    defaults = [
        ("read_10_pages", "10 sahifa o'qing", "pages_read", 10, 30),
        ("invite_1_friend", "1 do'st taklif qiling", "referrals", 1, 50),
        ("streak_3_days", "3 kun ketma-ket kiring", "streak_days", 3, 40),
        ("quiz_10_correct", "Testda 10 ta to'g'ri javob bering", "quiz_correct", 10, 60),
    ]
    for code, title, task_type, goal, reward in defaults:
        await conn.execute(
            "INSERT INTO tasks (code, title, task_type, goal, reward, active) "
            "VALUES ($1, $2, $3, $4, $5, TRUE) ON CONFLICT (code) DO NOTHING",
            code, title, task_type, goal, reward,
        )


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


async def mark_referral_rewarded(user_id: int, reward_date: str = None):
    if reward_date is None:
        reward_date = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET referral_rewarded = TRUE, referral_reward_date = $1 WHERE user_id = $2",
            reward_date, user_id,
        )


async def get_referral_rewards_today_count(inviter_id: int, date_str: str) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) as cnt FROM users
            WHERE referred_by = $1 AND referral_rewarded = TRUE AND referral_reward_date = $2
            """,
            inviter_id, date_str,
        )
        return row["cnt"] if row else 0


async def get_pending_referrals(limit: int = 200):
    """Telefon ulangan, kanalga a'zo bo'lgan (botdan foydalanayotgan), lekin hali
    bonus berilmagan referallar ro'yxati — eng erta qo'shilganlardan boshlab."""
    async with _pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT user_id, referred_by FROM users
            WHERE referred_by IS NOT NULL
              AND referral_rewarded = FALSE
              AND phone IS NOT NULL
            ORDER BY joined_date ASC
            LIMIT $1
            """,
            limit,
        )


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


# ---------- BEPUL KUNLIK BONUS (hammaga) ----------

async def has_claimed_free_daily_bonus(user_id: int) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_free_daily_bonus_date FROM users WHERE user_id = $1", user_id
        )
        return bool(row and row["last_free_daily_bonus_date"] == today)


async def claim_free_daily_bonus(user_id: int, amount: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_free_daily_bonus_date FROM users WHERE user_id = $1", user_id
        )
        if row and row["last_free_daily_bonus_date"] == today:
            return 0
        await conn.execute(
            "UPDATE users SET coins = coins + $1, last_free_daily_bonus_date = $2 WHERE user_id = $3",
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


async def get_referral_rank(user_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH counts AS (
                SELECT referred_by AS uid, COUNT(*) AS cnt
                FROM users
                WHERE referred_by IS NOT NULL
                GROUP BY referred_by
            ),
            ranked AS (
                SELECT uid, cnt, RANK() OVER (ORDER BY cnt DESC) AS rnk
                FROM counts
            )
            SELECT rnk FROM ranked WHERE uid = $1
            """,
            user_id,
        )
        return row["rnk"] if row else None


async def get_coins_rank(user_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH ranked AS (
                SELECT user_id, RANK() OVER (ORDER BY coins DESC) AS rnk FROM users
            )
            SELECT rnk FROM ranked WHERE user_id = $1
            """,
            user_id,
        )
        return row["rnk"] if row else None


async def get_quiz_rank(user_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH ranked AS (
                SELECT user_id, RANK() OVER (ORDER BY quiz_score_total DESC) AS rnk FROM users
            )
            SELECT rnk FROM ranked WHERE user_id = $1
            """,
            user_id,
        )
        return row["rnk"] if row else None


async def get_quiz_leaderboard(limit: int = 10):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT user_id, username, quiz_score_total AS cnt FROM users "
            "WHERE quiz_score_total > 0 ORDER BY quiz_score_total DESC LIMIT $1",
            limit,
        )


async def increment_quiz_stats(user_id: int, questions_answered: int, correct: int, score_earned: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users SET
                total_quiz_attempts = total_quiz_attempts + 1,
                total_quiz_questions = total_quiz_questions + $1,
                quiz_correct_total = quiz_correct_total + $2,
                quiz_score_total = quiz_score_total + $3
            WHERE user_id = $4
            """,
            questions_answered, correct, score_earned, user_id,
        )


async def get_completed_books_count(user_id: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) as cnt FROM user_book_progress p
            JOIN books b ON b.id = p.book_id
            WHERE p.user_id = $1 AND b.page_count > 0 AND p.current_page >= b.page_count
            """,
            user_id,
        )
        return row["cnt"] if row else 0


async def set_user_region(user_id: int, region: str):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE users SET region = $1 WHERE user_id = $2", region, user_id)


async def set_user_phone(user_id: int, phone: str):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE users SET phone = $1 WHERE user_id = $2", phone, user_id)


async def set_user_avatar(user_id: int, avatar_path: str):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE users SET avatar_path = $1 WHERE user_id = $2", avatar_path, user_id)


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


# ---------- VAZIFALAR (TASKS) ----------

async def add_task(code: str, title: str, task_type: str, goal: int, reward: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO tasks (code, title, task_type, goal, reward, active) "
            "VALUES ($1, $2, $3, $4, $5, TRUE) RETURNING id",
            code, title, task_type, goal, reward,
        )
        return row["id"]


async def get_active_tasks():
    async with _pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM tasks WHERE active = TRUE ORDER BY id")


async def get_claimed_task_ids(user_id: int) -> set:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT task_id FROM user_task_progress WHERE user_id = $1", user_id
        )
        return {r["task_id"] for r in rows}


async def get_tasks_with_progress(user_id: int):
    user = await get_user(user_id)
    tasks = await get_active_tasks()
    claimed_ids = await get_claimed_task_ids(user_id)

    quiz_total = (user["quiz_correct_total"] or 0) if user else 0
    pages = (user["pages_read"] or 0) if user else 0
    streak = (user["streak_days"] or 0) if user else 0
    referrals = await get_referral_count(user_id)

    progress_map = {
        "pages_read": pages,
        "referrals": referrals,
        "streak_days": streak,
        "quiz_correct": quiz_total,
    }

    result = []
    for t in tasks:
        current = progress_map.get(t["task_type"], 0)
        result.append({
            "id": t["id"],
            "code": t["code"],
            "title": t["title"],
            "goal": t["goal"],
            "reward": t["reward"],
            "progress": min(current, t["goal"]),
            "completed": current >= t["goal"],
            "claimed": t["id"] in claimed_ids,
        })
    return result


async def claim_task(user_id: int, task_code: str):
    async with _pool.acquire() as conn:
        task = await conn.fetchrow(
            "SELECT * FROM tasks WHERE code = $1 AND active = TRUE", task_code
        )
        if not task:
            return False, "invalid_task", None

        already = await conn.fetchrow(
            "SELECT 1 FROM user_task_progress WHERE user_id = $1 AND task_id = $2",
            user_id, task["id"],
        )
        if already:
            return False, "already_claimed", None

        progress_list = await get_tasks_with_progress(user_id)
        current_task = next((t for t in progress_list if t["id"] == task["id"]), None)
        if not current_task or not current_task["completed"]:
            return False, "not_completed", None

        await conn.execute(
            "INSERT INTO user_task_progress (user_id, task_id, claimed_at) VALUES ($1, $2, $3)",
            user_id, task["id"], datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        new_balance = await add_coins(user_id, task["reward"])
        return True, task["reward"], new_balance


async def increment_quiz_correct_total(user_id: int, amount: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET quiz_correct_total = quiz_correct_total + $1 WHERE user_id = $2",
            amount, user_id,
        )


# ---------- MUTOLAA TARIXI ----------

async def record_reading_event(user_id: int, book_title: str, page_number: int, coins_earned: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reading_history (user_id, book_title, page_number, coins_earned, read_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id, book_title, page_number, coins_earned,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


async def get_reading_history(user_id: int, limit: int = 20):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM reading_history WHERE user_id = $1 ORDER BY id DESC LIMIT $2",
            user_id, limit,
        )


# ---------- HAFTALIK/KUNLIK TANLOV ----------

async def get_current_weekly_draw():
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM weekly_draws WHERE active = TRUE ORDER BY id DESC LIMIT 1"
        )
        return row


async def get_draw_by_id(draw_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM weekly_draws WHERE id = $1", draw_id)


async def ensure_default_weekly_draw(default_goal: int, default_reward_text: str, default_days: int = 20):
    existing = await get_current_weekly_draw()
    if existing:
        return existing
    return await set_weekly_draw(default_goal, default_reward_text, default_days)


async def set_weekly_draw(goal: int, reward_text: str, days: int = 20,
                           required_channels: str = "", prizes: str = "", image_path: str = None):
    """Admin tomonidan joriy tanlovni yangilaydi. `days` — tanlov necha kun davom etishi.
    `required_channels` — vergul bilan ajratilgan kanal username'lari (masalan '@k1,@k2').
    `prizes` — har biri alohida qatorda sovg'alar ro'yxati."""
    today = datetime.now()
    week_start = today.strftime("%Y-%m-%d")
    week_end = (today + timedelta(days=days)).strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE weekly_draws SET active = FALSE WHERE active = TRUE")
        row = await conn.fetchrow(
            """
            INSERT INTO weekly_draws
                (title, goal, reward_text, week_start, week_end, active,
                 required_channels, prizes, image_path)
            VALUES ($1, $2, $3, $4, $5, TRUE, $6, $7, $8)
            RETURNING *
            """,
            "Tanlov", goal, reward_text, week_start, week_end,
            required_channels, prizes, image_path,
        )
        return row


async def set_draw_image_path(draw_id: int, image_path: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE weekly_draws SET image_path = $1 WHERE id = $2", image_path, draw_id
        )


async def get_random_eligible_winners(min_referrals: int, count: int):
    """Tasodifiy g'oliblarni tanlaydi. Agar min_referrals <= 0 bo'lsa,
    barcha foydalanuvchilar orasidan (referral shartisiz) tanlaydi."""
    async with _pool.acquire() as conn:
        if min_referrals <= 0:
            return await conn.fetch(
                "SELECT user_id, username, 0 as cnt FROM users ORDER BY RANDOM() LIMIT $1",
                count,
            )
        return await conn.fetch(
            """
            WITH counts AS (
                SELECT referred_by AS uid, COUNT(*) AS cnt
                FROM users
                WHERE referred_by IS NOT NULL
                GROUP BY referred_by
                HAVING COUNT(*) >= $1
            )
            SELECT u.user_id, u.username, c.cnt
            FROM counts c
            JOIN users u ON u.user_id = c.uid
            ORDER BY RANDOM()
            LIMIT $2
            """,
            min_referrals, count,
        )


async def get_weekly_referral_progress(user_id: int, week_start: str) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) as cnt FROM users
            WHERE referred_by = $1 AND joined_date >= $2
            """,
            user_id, week_start,
        )
        return row["cnt"] if row else 0


async def get_weekly_participant_count(week_start: str, goal: int) -> int:
    """Shu tanlov davrida shartni bajargan (yetarli referral qilgan) foydalanuvchilar soni."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) as cnt FROM (
                SELECT referred_by FROM users
                WHERE referred_by IS NOT NULL AND joined_date >= $1
                GROUP BY referred_by
                HAVING COUNT(*) >= $2
            ) sub
            """,
            week_start, goal,
        )
        return row["cnt"] if row else 0


# ---------- G'OLIBLAR ----------

async def add_winner(user_id: int, username: str, week_label: str, referral_count: int, prize: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO winners (user_id, username, week_label, referral_count, prize, won_date)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            user_id, username, week_label, referral_count, prize,
            datetime.now().strftime("%Y-%m-%d"),
        )


async def get_recent_winners(limit: int = 5):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM winners ORDER BY id DESC LIMIT $1", limit
        )


async def get_random_eligible_winner(min_referrals: int = 1):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH counts AS (
                SELECT referred_by AS uid, COUNT(*) AS cnt
                FROM users
                WHERE referred_by IS NOT NULL
                GROUP BY referred_by
                HAVING COUNT(*) >= $1
            )
            SELECT u.user_id, u.username, c.cnt
            FROM counts c
            JOIN users u ON u.user_id = c.uid
            ORDER BY RANDOM()
            LIMIT 1
            """,
            min_referrals,
        )
        return row


# ---------- KITOBLAR (PDF) ----------

async def add_book(title: str, author: str, genre: str, cover_path: str,
                    required_referrals: int, required_coins: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO books (title, author, genre, cover_path, page_count,
                                required_referrals, required_coins, added_at, active)
            VALUES ($1, $2, $3, $4, 0, $5, $6, $7, TRUE)
            RETURNING id
            """,
            title, author, genre, cover_path, required_referrals, required_coins,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        return row["id"]


async def set_book_cover_path(book_id: int, cover_path: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE books SET cover_path = $1 WHERE id = $2", cover_path, book_id
        )


async def add_book_pages(book_id: int, image_paths: list):
    async with _pool.acquire() as conn:
        for i, path in enumerate(image_paths, start=1):
            await conn.execute(
                "INSERT INTO book_pages (book_id, page_number, image_path) VALUES ($1, $2, $3) "
                "ON CONFLICT (book_id, page_number) DO UPDATE SET image_path = EXCLUDED.image_path",
                book_id, i, path,
            )
        await conn.execute(
            "UPDATE books SET page_count = $1 WHERE id = $2", len(image_paths), book_id
        )


async def get_all_books():
    async with _pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM books WHERE active = TRUE ORDER BY id DESC")


async def get_book(book_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM books WHERE id = $1", book_id)


async def get_book_pages(book_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM book_pages WHERE book_id = $1 ORDER BY page_number", book_id
        )


async def get_book_page(book_id: int, page_number: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM book_pages WHERE book_id = $1 AND page_number = $2",
            book_id, page_number,
        )


async def is_book_unlocked(user_id: int, book_id: int) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM user_book_unlocks WHERE user_id = $1 AND book_id = $2",
            user_id, book_id,
        )
        return row is not None


async def unlock_book(user_id: int, book_id: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_book_unlocks (user_id, book_id, unlocked_at) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id, book_id) DO NOTHING",
            user_id, book_id, datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


async def get_user_unlocked_book_ids(user_id: int) -> set:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT book_id FROM user_book_unlocks WHERE user_id = $1", user_id
        )
        return {r["book_id"] for r in rows}


async def get_book_progress(user_id: int, book_id: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT current_page FROM user_book_progress WHERE user_id = $1 AND book_id = $2",
            user_id, book_id,
        )
        return row["current_page"] if row else 0


async def set_book_progress(user_id: int, book_id: int, page: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_book_progress (user_id, book_id, current_page)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, book_id) DO UPDATE SET current_page = EXCLUDED.current_page
            """,
            user_id, book_id, page,
        )


async def has_read_page(user_id: int, book_id: int, page_number: int) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM user_page_reads WHERE user_id = $1 AND book_id = $2 AND page_number = $3",
            user_id, book_id, page_number,
        )
        return row is not None


async def mark_page_read(user_id: int, book_id: int, page_number: int):
    """Sahifa birinchi marta o'qilganini belgilaydi. Faqat shu chaqiriqda koin berish kerakligini
    aniqlash uchun ishlatiladi — chaqiruvchi avval has_read_page bilan tekshirishi kerak."""
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_page_reads (user_id, book_id, page_number, read_at) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (user_id, book_id, page_number) DO NOTHING",
            user_id, book_id, page_number, datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


async def delete_book(book_id: int):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE books SET active = FALSE WHERE id = $1", book_id)
