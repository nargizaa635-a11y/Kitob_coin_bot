# database.py
# Postgres (Railway) — to'liq yangilangan versiya
# Iqtisodiyot + Premium (Stars) + mavjud barcha funksiyalar

import os
import random
import asyncpg
from datetime import datetime, timedelta
from decimal import Decimal

DATABASE_URL = os.environ.get("DATABASE_URL")

_pool: asyncpg.Pool = None

# ================== SOZLAMALAR ==================
DEFAULT_MARGIN = 0.18          # 18% marja
STAR_TO_COIN = 100             # 1 Star = 100 Koin
NET_USD_PER_STAR = 0.013       # Senga net tushum


async def init_db():
    global _pool
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL topilmadi! Railway'da 'web' xizmatiga DATABASE_URL "
            "o'zgaruvchisini qo'shganingizni tekshiring."
        )
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=20)

    async with _pool.acquire() as conn:
        # ---------- USERS ----------
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
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_quiz_attempts INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_quiz_questions INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quiz_score_total INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS region TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_path TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS signup_ip TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS streak_freezes INTEGER DEFAULT 2")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS xp INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS level INTEGER DEFAULT 1")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_reading_seconds INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_reading_date TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_reading_claimed BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_premium_days INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_reminder_sent BOOLEAN DEFAULT FALSE")

        # ---------- CHALLENGE TIZIMI ----------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS challenges (
                id SERIAL PRIMARY KEY,
                book_title TEXT NOT NULL,
                cover_path TEXT,
                description TEXT,
                days_count INTEGER NOT NULL,
                pass_percent INTEGER DEFAULT 80,
                status TEXT DEFAULT 'active',
                created_at TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS challenge_days (
                id SERIAL PRIMARY KEY,
                challenge_id INTEGER REFERENCES challenges(id),
                day_number INTEGER NOT NULL,
                reading_text TEXT,
                UNIQUE(challenge_id, day_number)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS challenge_questions (
                id SERIAL PRIMARY KEY,
                challenge_id INTEGER REFERENCES challenges(id),
                day_number INTEGER,
                question TEXT NOT NULL,
                option_a TEXT, option_b TEXT, option_c TEXT, option_d TEXT,
                correct_option TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS challenge_participants (
                id SERIAL PRIMARY KEY,
                challenge_id INTEGER REFERENCES challenges(id),
                user_id BIGINT NOT NULL,
                joined_at TEXT,
                current_day INTEGER DEFAULT 1,
                streak INTEGER DEFAULT 0,
                total_xp INTEGER DEFAULT 0,
                total_coins INTEGER DEFAULT 0,
                final_correct INTEGER,
                final_total INTEGER,
                final_percent NUMERIC,
                completed_at TEXT,
                status TEXT DEFAULT 'in_progress',
                UNIQUE(challenge_id, user_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS challenge_daily_progress (
                id SERIAL PRIMARY KEY,
                participant_id INTEGER REFERENCES challenge_participants(id),
                day_number INTEGER,
                completed_at TEXT,
                correct_count INTEGER,
                total_count INTEGER,
                UNIQUE(participant_id, day_number)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS challenge_top_bonuses (
                id SERIAL PRIMARY KEY,
                challenge_id INTEGER REFERENCES challenges(id),
                rank INTEGER,
                coin_reward INTEGER,
                xp_reward INTEGER
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS certificates (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                user_id BIGINT NOT NULL,
                challenge_id INTEGER,
                book_title TEXT,
                percent NUMERIC,
                issued_at TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS badges (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                challenge_id INTEGER,
                badge_name TEXT,
                issued_at TEXT
            )
        """)
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS freeze_refill_month TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS has_seen_onboarding BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS active_hour INTEGER")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_reward_date TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_referrals INTEGER DEFAULT 0")

        # ---------- PURCHASES ----------
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

        # ---------- CHEST OPENS ----------
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

        # ---------- TASKS ----------
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

        # ---------- READING HISTORY ----------
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

        # ---------- WEEKLY DRAWS ----------
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

        # ---------- WINNERS ----------
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

        # ---------- BOOKS ----------
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
        await conn.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10,2) DEFAULT 0")
        await conn.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS premium_only BOOLEAN DEFAULT FALSE")
        # Audio kitoblar uchun: book_type = 'pdf' (odatiy) yoki 'audio'
        await conn.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS book_type TEXT DEFAULT 'pdf'")
        await conn.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS chapter_count INTEGER DEFAULT 0")

        # ---------- AUDIO KITOB BOBLARI ----------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS book_audio_chapters (
                id SERIAL PRIMARY KEY,
                book_id INTEGER NOT NULL REFERENCES books(id),
                chapter_number INTEGER NOT NULL,
                title TEXT,
                audio_path TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL DEFAULT 0,
                UNIQUE (book_id, chapter_number)
            )
        """)

        # ---------- FOYDALANUVCHI AUDIO PROGRESSI ----------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_audio_progress (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                book_id INTEGER NOT NULL REFERENCES books(id),
                chapter_number INTEGER NOT NULL DEFAULT 1,
                position_seconds INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT,
                UNIQUE (user_id, book_id)
            )
        """)

        # ---------- FOYDALANUVCHI TUGATGAN AUDIO BOBLARI (mukofot uchun, bir marta) ----------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_audio_chapter_reads (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                book_id INTEGER NOT NULL,
                chapter_number INTEGER NOT NULL,
                read_at TEXT,
                UNIQUE (user_id, book_id, chapter_number)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS book_pages (
                id SERIAL PRIMARY KEY,
                book_id INTEGER NOT NULL REFERENCES books(id),
                page_number INTEGER NOT NULL,
                image_path TEXT,
                UNIQUE (book_id, page_number)
            )
        """)
        await conn.execute("ALTER TABLE book_pages ALTER COLUMN image_path DROP NOT NULL")
        await conn.execute("ALTER TABLE book_pages ADD COLUMN IF NOT EXISTS page_type TEXT DEFAULT 'image'")
        await conn.execute("ALTER TABLE book_pages ADD COLUMN IF NOT EXISTS text_content TEXT")

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

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_book_milestones (
                user_id BIGINT NOT NULL,
                book_id INTEGER NOT NULL REFERENCES books(id),
                milestones_claimed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, book_id)
            )
        """)


        # ---------- SHOP PRODUCTS ----------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS shop_products (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                category TEXT NOT NULL,
                image_path TEXT,
                cost INTEGER NOT NULL,
                required_referrals INTEGER NOT NULL DEFAULT 0,
                stock INTEGER,
                active BOOLEAN DEFAULT TRUE,
                created_at TEXT
            )
        """)
        await conn.execute("ALTER TABLE shop_products ALTER COLUMN cost DROP NOT NULL")
        await conn.execute("ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS payment_type TEXT DEFAULT 'coin'")
        await conn.execute("ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS price_uzs INTEGER")
        await conn.execute("ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10,2) DEFAULT 0")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS shop_purchases (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                product_id INTEGER NOT NULL REFERENCES shop_products(id),
                cost_paid INTEGER NOT NULL,
                status TEXT DEFAULT 'kutilmoqda',
                purchased_at TEXT NOT NULL
            )
        """)
        await conn.execute("ALTER TABLE shop_purchases ADD COLUMN IF NOT EXISTS payment_type TEXT DEFAULT 'coin'")
        await conn.execute("ALTER TABLE shop_purchases ADD COLUMN IF NOT EXISTS contact_phone TEXT")
        await conn.execute("ALTER TABLE shop_purchases ADD COLUMN IF NOT EXISTS contact_username TEXT")
        await conn.execute("ALTER TABLE shop_purchases ADD COLUMN IF NOT EXISTS admin_note TEXT")

        # ---------- FORCE CHANNELS ----------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS force_channels (
                id SERIAL PRIMARY KEY,
                channel_username TEXT UNIQUE NOT NULL,
                active BOOLEAN DEFAULT TRUE,
                added_at TEXT
            )
        """)

        # ---------- QUIZ ----------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS quiz_sets (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                required_referrals INTEGER NOT NULL DEFAULT 0,
                reward_per_correct INTEGER NOT NULL DEFAULT 3,
                question_timer_seconds INTEGER NOT NULL DEFAULT 20,
                active BOOLEAN DEFAULT TRUE,
                created_at TEXT
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS quiz_questions (
                id SERIAL PRIMARY KEY,
                quiz_set_id INTEGER NOT NULL REFERENCES quiz_sets(id),
                order_index INTEGER NOT NULL,
                question_text TEXT NOT NULL,
                option_a TEXT NOT NULL,
                option_b TEXT NOT NULL,
                option_c TEXT NOT NULL,
                option_d TEXT NOT NULL,
                correct_option TEXT NOT NULL
            )
        """)

        # ---------- YANGI: PREMIUM ORDERS ----------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS premium_orders (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                months INTEGER NOT NULL,
                stars_paid INTEGER NOT NULL,
                status TEXT DEFAULT 'kutilmoqda',
                created_at TEXT,
                completed_at TEXT,
                admin_note TEXT
            )
        """)

        # ---------- YANGI: STARS TO'LOVLARI ----------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS star_payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount_stars INTEGER NOT NULL,
                purpose TEXT,
                payload TEXT,
                telegram_payment_charge_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)

        # ---------- YANGI: SANDIQLAR (bazaga ko'chirildi) ----------
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chests (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                emoji TEXT NOT NULL,
                cost INTEGER NOT NULL,
                daily_limit INTEGER NOT NULL,
                rewards_json TEXT NOT NULL,
                active BOOLEAN DEFAULT TRUE,
                sort_order INTEGER DEFAULT 0
            )
        """)

        # ---------- YANGI: TANNARX ASOSIDA AVTOMATIK NARXLASH ----------
        # shop_products va books jadvallariga tannarx ($) va marja ustunlari qo'shamiz (mavjud bo'lmasa)
        await conn.execute("ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS cost_usd NUMERIC")
        await conn.execute("ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS margin_percent NUMERIC DEFAULT 17")
        await conn.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS cost_usd NUMERIC")
        await conn.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS margin_percent NUMERIC DEFAULT 17")

        await _seed_default_chests(conn)
        await _seed_default_tasks(conn)


async def _seed_default_chests(conn):
    existing = await conn.fetchrow("SELECT 1 FROM chests LIMIT 1")
    if existing:
        return
    import json as _json
    defaults = [
        ("oddiiy", "Oddiy sandiq", "📦", 40, 5,
         [{"type": "coins", "min": 15, "max": 70, "chance": 100}], 1),
        ("oltin", "Oltin sandiq", "🥇", 120, 3,
         [{"type": "coins", "min": 80, "max": 200, "chance": 70},
          {"type": "premium", "days": 1, "chance": 30}], 2),
        ("legend", "Legend sandiq", "👑", 250, 2,
         [{"type": "coins", "min": 150, "max": 400, "chance": 50},
          {"type": "premium", "days": 3, "chance": 50}], 3),
    ]
    for chest_id, name, emoji, cost, daily_limit, rewards, sort_order in defaults:
        await conn.execute(
            """INSERT INTO chests (id, name, emoji, cost, daily_limit, rewards_json, active, sort_order)
               VALUES ($1, $2, $3, $4, $5, $6, TRUE, $7)
               ON CONFLICT (id) DO NOTHING""",
            chest_id, name, emoji, cost, daily_limit, _json.dumps(rewards), sort_order,
        )


async def get_active_chests() -> list:
    import json as _json
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM chests WHERE active = TRUE ORDER BY sort_order ASC")
        result = []
        for r in rows:
            d = dict(r)
            d["rewards"] = _json.loads(d["rewards_json"])
            result.append(d)
        return result


async def get_chest_by_id(chest_id: str):
    import json as _json
    async with _pool.acquire() as conn:
        r = await conn.fetchrow("SELECT * FROM chests WHERE id = $1", chest_id)
        if not r:
            return None
        d = dict(r)
        d["rewards"] = _json.loads(d["rewards_json"])
        return d


async def add_chest(chest_id: str, name: str, emoji: str, cost: int, daily_limit: int, rewards: list, sort_order: int = 0):
    import json as _json
    async with _pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO chests (id, name, emoji, cost, daily_limit, rewards_json, active, sort_order)
               VALUES ($1, $2, $3, $4, $5, $6, TRUE, $7)
               ON CONFLICT (id) DO UPDATE SET
                 name = $2, emoji = $3, cost = $4, daily_limit = $5, rewards_json = $6, sort_order = $7""",
            chest_id, name, emoji, cost, daily_limit, _json.dumps(rewards), sort_order,
        )


async def deactivate_chest(chest_id: str):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE chests SET active = FALSE WHERE id = $1", chest_id)



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


# ================== NARXLASH ==================
# Eslatma: haqiqiy calculate_coin_price funksiyasi main.py ichida joylashgan
# (STARS_PER_USD/COINS_PER_STAR konstantalari bilan). Bu yerda takrorlanmasin
# deb olib tashlandi — chalkashlikning oldini olish uchun.


# ================== FOYDALANUVCHILAR ==================

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


async def set_user_phone(user_id: int, phone: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET phone = $1 WHERE user_id = $2", phone, user_id
        )


async def set_user_region(user_id: int, region: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET region = $1 WHERE user_id = $2", region, user_id
        )


async def set_user_avatar(user_id: int, avatar_path: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET avatar_path = $1 WHERE user_id = $2", avatar_path, user_id
        )


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


async def get_users_for_reminder(inactive_days: int = 1, target_hour: int = None):
    """Nofaol foydalanuvchilarni topadi. Agar target_hour berilgan bo'lsa,
    faqat o'sha soatda odatda faol bo'lgan foydalanuvchilarga eslatma yuboriladi
    (shaxsiylashtirilgan vaqt) — bu ochilish foizini oshiradi."""
    cutoff = (datetime.now() - timedelta(days=inactive_days)).strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        if target_hour is not None:
            return await conn.fetch(
                """
                SELECT user_id FROM users
                WHERE (last_active_date IS NULL OR last_active_date < $1)
                  AND (last_reminder_date IS NULL OR last_reminder_date < $1)
                  AND active_hour = $2
                LIMIT 100
                """,
                cutoff, target_hour,
            )
        return await conn.fetch(
            """
            SELECT user_id FROM users
            WHERE (last_active_date IS NULL OR last_active_date < $1)
              AND (last_reminder_date IS NULL OR last_reminder_date < $1)
              AND active_hour IS NULL
            LIMIT 100
            """,
            cutoff,
        )


async def mark_reminder_sent(user_id: int):
    today = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET last_reminder_date = $1 WHERE user_id = $2", today, user_id
        )


async def get_users_for_premium_reminder():
    """Premium'i ertaga (keyingi 24 soat ichida) tugaydigan va hali eslatma
    yuborilmagan foydalanuvchilarni topadi."""
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    async with _pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT user_id, premium_expires_at FROM users
            WHERE is_premium = TRUE
              AND premium_expires_at IS NOT NULL
              AND premium_expires_at BETWEEN $1 AND $2
              AND (premium_reminder_sent IS NULL OR premium_reminder_sent = FALSE)
            """,
            now, tomorrow,
        )


async def mark_premium_reminder_sent(user_id: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET premium_reminder_sent = TRUE WHERE user_id = $1", user_id
        )


# ================== STATISTIKA ==================

async def increment_pages_read(user_id: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET pages_read = pages_read + 1 WHERE user_id = $1 RETURNING pages_read",
            user_id,
        )
        return row["pages_read"] if row else 0


async def update_streak(user_id: int) -> dict:
    """Streak'ni yangilaydi. 1 kun o'tkazib yuborilsa va 'muzlatuvchi' (freeze) mavjud
    bo'lsa, seriya buzilmaydi — freeze avtomatik ishlatiladi. Har oyning 1-kunida
    freeze zaxirasi 2 taga to'ldiriladi."""
    today = datetime.now().strftime("%Y-%m-%d")
    this_month = datetime.now().strftime("%Y-%m")
    async with _pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT last_active_date, streak_days, streak_freezes, freeze_refill_month "
            "FROM users WHERE user_id = $1", user_id
        )
        if not user:
            return {"streak": 0, "freeze_used": False, "freezes_left": 0}

        # Oylik freeze to'ldirish
        freezes = user["streak_freezes"] if user["streak_freezes"] is not None else 2
        if user["freeze_refill_month"] != this_month:
            freezes = 2
            await conn.execute(
                "UPDATE users SET streak_freezes = $1, freeze_refill_month = $2 WHERE user_id = $3",
                freezes, this_month, user_id,
            )

        last_date = user["last_active_date"]
        streak = user["streak_days"] or 0

        if last_date == today:
            return {"streak": streak, "freeze_used": False, "freezes_left": freezes}

        freeze_used = False
        if last_date:
            try:
                last_dt = datetime.strptime(last_date, "%Y-%m-%d")
                today_dt = datetime.strptime(today, "%Y-%m-%d")
                diff_days = (today_dt - last_dt).days
            except Exception:
                diff_days = None

            if diff_days == 1:
                streak = streak + 1
            elif diff_days == 2 and freezes > 0:
                streak = streak + 1
                freezes -= 1
                freeze_used = True
            else:
                streak = 1
        else:
            streak = 1

        await conn.execute(
            "UPDATE users SET streak_days = $1, last_active_date = $2, streak_freezes = $3 WHERE user_id = $4",
            streak, today, freezes, user_id,
        )
        return {"streak": streak, "freeze_used": freeze_used, "freezes_left": freezes}


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


# ================== PREMIUM ==================

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
            "UPDATE users SET is_premium = TRUE, premium_expires_at = $1, "
            "total_premium_days = total_premium_days + $2, premium_reminder_sent = FALSE "
            "WHERE user_id = $3",
            new_expiry, days, user_id,
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


# ================== REFERRAL ==================

async def get_referral_count(user_id: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(r.user_id) + COALESCE(u.bonus_referrals, 0) as cnt
            FROM users u
            LEFT JOIN users r ON r.referred_by = u.user_id
            WHERE u.user_id = $1
            GROUP BY u.bonus_referrals
            """,
            user_id,
        )
        return row["cnt"] if row else 0


async def add_bonus_referrals(user_id: int, amount: int) -> int:
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET bonus_referrals = COALESCE(bonus_referrals, 0) + $1 WHERE user_id = $2",
            amount, user_id,
        )
    return await get_referral_count(user_id)


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
                SELECT u.user_id AS uid,
                       COUNT(r.user_id) + COALESCE(u.bonus_referrals, 0) AS cnt
                FROM users u
                LEFT JOIN users r ON r.referred_by = u.user_id
                GROUP BY u.user_id, u.bonus_referrals
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


async def get_referral_rank_gap(user_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH counts AS (
                SELECT u.user_id AS uid, u.username AS uname,
                       COUNT(r.user_id) + COALESCE(u.bonus_referrals, 0) AS cnt
                FROM users u
                LEFT JOIN users r ON r.referred_by = u.user_id
                GROUP BY u.user_id, u.username, u.bonus_referrals
            ),
            ranked AS (
                SELECT uid, uname, cnt,
                       RANK() OVER (ORDER BY cnt DESC) AS rnk,
                       LAG(cnt) OVER (ORDER BY cnt DESC) AS next_value,
                       LAG(uname) OVER (ORDER BY cnt DESC) AS next_username
                FROM counts
            )
            SELECT rnk, cnt AS my_value, next_value, next_username
            FROM ranked WHERE uid = $1
            """,
            user_id,
        )
        if not row or row["next_value"] is None:
            return None
        return {
            "my_rank": row["rnk"],
            "my_value": row["my_value"],
            "next_value": row["next_value"],
            "next_username": row["next_username"],
            "gap": max(0, row["next_value"] - row["my_value"] + 1),
        }


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


async def get_coins_rank_gap(user_id: int):
    """Foydalanuvchining koin bo'yicha o'rnini va undan bitta yuqoridagi
    odamgacha qancha koin qolganini qaytaradi. Agar u #1 bo'lsa None qaytadi."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH ranked AS (
                SELECT user_id, username, coins,
                       RANK() OVER (ORDER BY coins DESC) AS rnk,
                       LAG(coins) OVER (ORDER BY coins DESC) AS next_value,
                       LAG(username) OVER (ORDER BY coins DESC) AS next_username,
                       LAG(user_id) OVER (ORDER BY coins DESC) AS next_user_id
                FROM users
            )
            SELECT rnk, coins AS my_value, next_value, next_username, next_user_id
            FROM ranked WHERE user_id = $1
            """,
            user_id,
        )
        if not row or row["next_value"] is None:
            return None
        return {
            "my_rank": row["rnk"],
            "my_value": row["my_value"],
            "next_value": row["next_value"],
            "next_username": row["next_username"],
            "gap": max(0, row["next_value"] - row["my_value"] + 1),
        }


async def get_quiz_rank(user_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH ranked AS (
                SELECT user_id, RANK() OVER (ORDER BY COALESCE(quiz_score_total, 0) DESC) AS rnk FROM users
            )
            SELECT rnk FROM ranked WHERE user_id = $1
            """,
            user_id,
        )
        return row["rnk"] if row else None


async def get_quiz_rank_gap(user_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH ranked AS (
                SELECT user_id, username, COALESCE(quiz_score_total, 0) AS score,
                       RANK() OVER (ORDER BY COALESCE(quiz_score_total, 0) DESC) AS rnk,
                       LAG(COALESCE(quiz_score_total, 0)) OVER (ORDER BY COALESCE(quiz_score_total, 0) DESC) AS next_value,
                       LAG(username) OVER (ORDER BY COALESCE(quiz_score_total, 0) DESC) AS next_username
                FROM users
            )
            SELECT rnk, score AS my_value, next_value, next_username
            FROM ranked WHERE user_id = $1
            """,
            user_id,
        )
        if not row or row["next_value"] is None:
            return None
        return {
            "my_rank": row["rnk"],
            "my_value": row["my_value"],
            "next_value": row["next_value"],
            "next_username": row["next_username"],
            "gap": max(0, row["next_value"] - row["my_value"] + 1),
        }


async def get_coins_leaderboard(limit: int = 10):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT user_id, username, avatar_path, coins, is_premium, premium_expires_at, "
            "COALESCE(level, 1) AS level "
            "FROM users ORDER BY coins DESC LIMIT $1", limit
        )


async def get_referral_leaderboard(limit: int = 10):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT u.user_id, u.username, u.avatar_path, u.is_premium, u.premium_expires_at, COUNT(r.user_id) as cnt
            FROM users u
            JOIN users r ON r.referred_by = u.user_id
            GROUP BY u.user_id, u.username, u.avatar_path, u.is_premium, u.premium_expires_at
            ORDER BY cnt DESC
            LIMIT $1
            """,
            limit,
        )


# ================== PREMIUM ORDERS & STARS ==================

async def create_premium_order(user_id: int, months: int, stars_paid: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO premium_orders (user_id, months, stars_paid, status, created_at)
            VALUES ($1, $2, $3, 'kutilmoqda', $4)
            RETURNING id
            """,
            user_id, months, stars_paid, datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        return row["id"]


async def get_pending_premium_orders(limit: int = 50):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT po.*, u.username
            FROM premium_orders po
            JOIN users u ON u.user_id = po.user_id
            WHERE po.status = 'kutilmoqda'
            ORDER BY po.id ASC
            LIMIT $1
            """,
            limit,
        )


async def complete_premium_order(order_id: int, admin_note: str = None):
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE premium_orders
            SET status = 'bajarildi', completed_at = $1, admin_note = $2
            WHERE id = $3
            """,
            datetime.now().strftime("%Y-%m-%d %H:%M"), admin_note, order_id,
        )


async def record_star_payment(user_id: int, amount_stars: int, purpose: str,
                               payload: str = None, charge_id: str = None):
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO star_payments
                (user_id, amount_stars, purpose, payload, telegram_payment_charge_id, status, created_at)
            VALUES ($1, $2, $3, $4, $5, 'completed', $6)
            """,
            user_id, amount_stars, purpose, payload, charge_id,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


# ================== TASKS ==================

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


# ================== READING HISTORY ==================

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


# ================== WEEKLY DRAW ==================

async def get_current_weekly_draw():
    async with _pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM weekly_draws WHERE active = TRUE ORDER BY id DESC LIMIT 1"
        )


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


# ================== WINNERS ==================

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


# ================== BOOKS ==================

async def add_book(title: str, author: str, genre: str, cover_path: str,
                    required_referrals: int, required_coins: int, cost_usd: float = 0,
                    book_type: str = "pdf") -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO books (title, author, genre, cover_path, page_count,
                                required_referrals, required_coins, cost_usd, added_at, active, book_type)
            VALUES ($1, $2, $3, $4, 0, $5, $6, $7, $8, TRUE, $9)
            RETURNING id
            """,
            title, author, genre, cover_path, required_referrals, required_coins, cost_usd,
            datetime.now().strftime("%Y-%m-%d %H:%M"), book_type,
        )
        return row["id"]


async def set_book_cover_path(book_id: int, cover_path: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE books SET cover_path = $1 WHERE id = $2", cover_path, book_id
        )


async def add_book_pages(book_id: int, image_paths: list):
    """Eski (faqat rasm) usul — moslik uchun saqlab qolindi."""
    async with _pool.acquire() as conn:
        for i, path in enumerate(image_paths, start=1):
            await conn.execute(
                "INSERT INTO book_pages (book_id, page_number, image_path, page_type) VALUES ($1, $2, $3, 'image') "
                "ON CONFLICT (book_id, page_number) DO UPDATE SET image_path = EXCLUDED.image_path, page_type = 'image'",
                book_id, i, path,
            )
        await conn.execute(
            "UPDATE books SET page_count = $1 WHERE id = $2", len(image_paths), book_id
        )


async def add_book_pages_hybrid(book_id: int, pages: list):
    """Gibrid usul: har bir element {'type': 'text', 'content': ...} yoki
    {'type': 'image', 'path': ...} bo'lishi mumkin."""
    async with _pool.acquire() as conn:
        for i, page in enumerate(pages, start=1):
            if page["type"] == "text":
                await conn.execute(
                    """INSERT INTO book_pages (book_id, page_number, page_type, text_content)
                       VALUES ($1, $2, 'text', $3)
                       ON CONFLICT (book_id, page_number) DO UPDATE
                       SET page_type = 'text', text_content = $3, image_path = NULL""",
                    book_id, i, page["content"],
                )
            else:
                await conn.execute(
                    """INSERT INTO book_pages (book_id, page_number, page_type, image_path)
                       VALUES ($1, $2, 'image', $3)
                       ON CONFLICT (book_id, page_number) DO UPDATE
                       SET page_type = 'image', image_path = $3, text_content = NULL""",
                    book_id, i, page["path"],
                )
        await conn.execute(
            "UPDATE books SET page_count = $1 WHERE id = $2", len(pages), book_id
        )


async def add_audio_chapters(book_id: int, chapters: list):
    """chapters: [{'title':..., 'path':..., 'duration_seconds':...}, ...] tartib bo'yicha."""
    async with _pool.acquire() as conn:
        for i, ch in enumerate(chapters, start=1):
            await conn.execute(
                """INSERT INTO book_audio_chapters (book_id, chapter_number, title, audio_path, duration_seconds)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (book_id, chapter_number) DO UPDATE
                   SET title = EXCLUDED.title, audio_path = EXCLUDED.audio_path,
                       duration_seconds = EXCLUDED.duration_seconds""",
                book_id, i, ch.get("title") or f"{i}-bob", ch["path"], ch.get("duration_seconds", 0),
            )
        await conn.execute(
            "UPDATE books SET chapter_count = $1 WHERE id = $2", len(chapters), book_id
        )


async def get_book_audio_chapters(book_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM book_audio_chapters WHERE book_id = $1 ORDER BY chapter_number", book_id
        )


async def get_book_audio_chapter(book_id: int, chapter_number: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM book_audio_chapters WHERE book_id = $1 AND chapter_number = $2",
            book_id, chapter_number,
        )


async def get_audio_progress(user_id: int, book_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM user_audio_progress WHERE user_id = $1 AND book_id = $2",
            user_id, book_id,
        )


async def set_audio_progress(user_id: int, book_id: int, chapter_number: int, position_seconds: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_audio_progress (user_id, book_id, chapter_number, position_seconds, updated_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id, book_id) DO UPDATE
            SET chapter_number = EXCLUDED.chapter_number,
                position_seconds = EXCLUDED.position_seconds,
                updated_at = EXCLUDED.updated_at
            """,
            user_id, book_id, chapter_number, position_seconds,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


async def has_read_audio_chapter(user_id: int, book_id: int, chapter_number: int) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM user_audio_chapter_reads WHERE user_id = $1 AND book_id = $2 AND chapter_number = $3",
            user_id, book_id, chapter_number,
        )
        return row is not None


async def mark_audio_chapter_read(user_id: int, book_id: int, chapter_number: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_audio_chapter_reads (user_id, book_id, chapter_number, read_at) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (user_id, book_id, chapter_number) DO NOTHING",
            user_id, book_id, chapter_number, datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


async def get_all_books():
    async with _pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM books WHERE active = TRUE ORDER BY id DESC")


async def get_book(book_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM books WHERE id = $1", book_id)


async def toggle_book_premium_only(book_id: int) -> bool:
    """Kitobning 'faqat premium a'zolarga' belgisini almashtiradi. Yangi holatni qaytaradi."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT premium_only FROM books WHERE id = $1", book_id)
        if row is None:
            return None
        new_value = not row["premium_only"]
        await conn.execute("UPDATE books SET premium_only = $1 WHERE id = $2", new_value, book_id)
        return new_value


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
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_page_reads (user_id, book_id, page_number, read_at) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (user_id, book_id, page_number) DO NOTHING",
            user_id, book_id, page_number, datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


async def get_read_pages_count(user_id: int, book_id: int) -> int:
    """Foydalanuvchi shu kitobda nechta TURLI (takrorlanmagan) sahifa o'qiganini qaytaradi."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM user_page_reads WHERE user_id = $1 AND book_id = $2",
            user_id, book_id,
        )
        return row["cnt"] if row else 0


async def claim_reading_milestones(user_id: int, book_id: int, milestone_size: int) -> int:
    """Sahifa-bonus chegarasidan (masalan har 25 sahifa) nechtasi YANGI o'tilganini
    hisoblaydi va shuni "olingan" deb belgilaydi — shu tufayli har bir chegara
    foydalanuvchiga FAQAT BIR MARTA beriladi, qayta o'qib chiqsa ham qayta bermaydi."""
    async with _pool.acquire() as conn:
        async with conn.transaction():
            count_row = await conn.fetchrow(
                "SELECT COUNT(*) as cnt FROM user_page_reads WHERE user_id = $1 AND book_id = $2",
                user_id, book_id,
            )
            read_count = count_row["cnt"] if count_row else 0
            target = read_count // milestone_size

            row = await conn.fetchrow(
                "SELECT milestones_claimed FROM user_book_milestones WHERE user_id = $1 AND book_id = $2",
                user_id, book_id,
            )
            claimed = row["milestones_claimed"] if row else 0

            if target <= claimed:
                return 0

            await conn.execute(
                "INSERT INTO user_book_milestones (user_id, book_id, milestones_claimed) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (user_id, book_id) DO UPDATE SET milestones_claimed = $3",
                user_id, book_id, target,
            )
            return target - claimed


async def delete_book(book_id: int):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE books SET active = FALSE WHERE id = $1", book_id)


async def get_completed_books_count(user_id: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) as cnt
            FROM user_book_progress ubp
            JOIN books b ON b.id = ubp.book_id
            WHERE ubp.user_id = $1 AND ubp.current_page >= b.page_count AND b.page_count > 0
            """,
            user_id,
        )
        return row["cnt"] if row else 0


# ================== SHOP PRODUCTS ==================

async def add_shop_product(name: str, description: str, category: str, cost: int,
                            required_referrals: int, stock, cost_usd: float = 0,
                            payment_type: str = "coin", price_uzs: int = None) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO shop_products
                (name, description, category, image_path, cost, required_referrals, stock,
                 cost_usd, payment_type, price_uzs, active, created_at)
            VALUES ($1, $2, $3, '', $4, $5, $6, $7, $8, $9, TRUE, $10)
            RETURNING id
            """,
            name, description, category, cost, required_referrals, stock, cost_usd,
            payment_type, price_uzs, datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        return row["id"]


async def set_shop_product_image_path(product_id: int, image_path: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE shop_products SET image_path = $1 WHERE id = $2", image_path, product_id
        )


async def get_active_shop_products():
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM shop_products WHERE active = TRUE ORDER BY id DESC"
        )


async def get_shop_product(product_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM shop_products WHERE id = $1", product_id)


async def restock_shop_product(product_id: int, new_stock):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE shop_products SET stock = $1 WHERE id = $2", new_stock, product_id
        )


async def record_shop_purchase(user_id: int, product_id: int, cost_paid: int,
                                payment_type: str = "coin", contact_phone: str = None,
                                contact_username: str = None) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO shop_purchases
                (user_id, product_id, cost_paid, status, purchased_at, payment_type, contact_phone, contact_username)
            VALUES ($1, $2, $3, 'kutilmoqda', $4, $5, $6, $7)
            RETURNING id
            """,
            user_id, product_id, cost_paid, datetime.now().strftime("%Y-%m-%d %H:%M"),
            payment_type, contact_phone, contact_username,
        )
        await conn.execute(
            "UPDATE shop_products SET stock = stock - 1 WHERE id = $1 AND stock IS NOT NULL",
            product_id,
        )
        return row["id"]


async def get_user_purchase_count(user_id: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM shop_purchases WHERE user_id = $1",
            user_id,
        )
        return row["cnt"] if row else 0


async def get_pending_uzs_orders(limit: int = 50):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT sp.*, p.name AS product_name, p.price_uzs
            FROM shop_purchases sp
            JOIN shop_products p ON p.id = sp.product_id
            WHERE sp.payment_type = 'uzs' AND sp.status = 'kutilmoqda'
            ORDER BY sp.id ASC
            LIMIT $1
            """,
            limit,
        )


async def complete_uzs_order(order_id: int, admin_note: str = None):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE shop_purchases SET status = 'bajarildi', admin_note = $2 WHERE id = $1",
            order_id, admin_note,
        )


async def get_recent_shop_orders(limit: int = 30):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT sp.id, sp.purchased_at, sp.cost_paid, sp.status,
                   u.user_id, u.username,
                   prod.name AS product_name
            FROM shop_purchases sp
            JOIN users u ON u.user_id = sp.user_id
            JOIN shop_products prod ON prod.id = sp.product_id
            ORDER BY sp.id DESC
            LIMIT $1
            """,
            limit,
        )


# ================== FORCE CHANNELS ==================

async def seed_force_channel_if_empty(channel_username: str):
    async with _pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT 1 FROM force_channels LIMIT 1")
        if existing:
            return
        await conn.execute(
            "INSERT INTO force_channels (channel_username, active, added_at) "
            "VALUES ($1, TRUE, $2) ON CONFLICT (channel_username) DO NOTHING",
            channel_username, datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


async def get_active_force_channels() -> list:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT channel_username FROM force_channels WHERE active = TRUE ORDER BY id"
        )
        return [r["channel_username"] for r in rows]


async def add_force_channel(channel_username: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO force_channels (channel_username, active, added_at)
            VALUES ($1, TRUE, $2)
            ON CONFLICT (channel_username) DO UPDATE SET active = TRUE
            """,
            channel_username, datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


async def remove_force_channel(channel_username: str) -> bool:
    async with _pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE force_channels SET active = FALSE WHERE LOWER(channel_username) = LOWER($1) AND active = TRUE",
            channel_username,
        )
        return result.endswith("1")


# ================== QUIZ ==================

async def add_quiz_set(title: str, required_referrals: int, reward_per_correct: int,
                        question_timer_seconds: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO quiz_sets (title, required_referrals, reward_per_correct,
                                    question_timer_seconds, active, created_at)
            VALUES ($1, $2, $3, $4, TRUE, $5)
            RETURNING id
            """,
            title, required_referrals, reward_per_correct, question_timer_seconds,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        return row["id"]


async def add_quiz_questions(quiz_set_id: int, questions: list):
    async with _pool.acquire() as conn:
        for i, q in enumerate(questions, start=1):
            await conn.execute(
                """
                INSERT INTO quiz_questions
                    (quiz_set_id, order_index, question_text, option_a, option_b,
                     option_c, option_d, correct_option)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                quiz_set_id, i, q["question"], q["a"], q["b"], q["c"], q["d"], q["correct"],
            )


async def get_active_quiz_sets():
    async with _pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT qs.*, COUNT(qq.id) as question_count
            FROM quiz_sets qs
            LEFT JOIN quiz_questions qq ON qq.quiz_set_id = qs.id
            WHERE qs.active = TRUE
            GROUP BY qs.id
            ORDER BY qs.id DESC
            """
        )


async def get_quiz_set(quiz_set_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM quiz_sets WHERE id = $1", quiz_set_id)


async def get_quiz_questions(quiz_set_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM quiz_questions WHERE quiz_set_id = $1 ORDER BY order_index",
            quiz_set_id,
        )


# ================== PUBLIC PROFILE ==================

async def get_user_public_profile(user_id: int):
    user = await get_user(user_id)
    if not user:
        return None
    referral_count = await get_referral_count(user_id)
    referral_rank = await get_referral_rank(user_id)
    coins_rank = await get_coins_rank(user_id)
    quiz_rank = await get_quiz_rank(user_id)
    completed_books = await get_completed_books_count(user_id)
    is_premium = await check_premium_status(user_id)
    return {
        "user_id": user_id,
        "username": user["username"],
        "avatar_path": user["avatar_path"],
        "coins": user["coins"],
        "pages_read": user["pages_read"] or 0,
        "streak_days": user["streak_days"] or 0,
        "is_premium": is_premium,
        "referral_count": referral_count,
        "referral_rank": referral_rank,
        "coins_rank": coins_rank,
        "quiz_rank": quiz_rank,
        "completed_books": completed_books,
        "quiz_score_total": user["quiz_score_total"] or 0,
    }
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


async def add_task(code: str, title: str, task_type: str, goal: int, reward: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO tasks (code, title, task_type, goal, reward, active) "
            "VALUES ($1, $2, $3, $4, $5, TRUE) RETURNING id",
            code, title, task_type, goal, reward,
        )
        return row["id"]


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


async def get_quiz_leaderboard(limit: int = 10):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT user_id, username, avatar_path, is_premium, premium_expires_at, quiz_score_total AS cnt FROM users "
            "WHERE quiz_score_total > 0 ORDER BY quiz_score_total DESC LIMIT $1",
            limit,
        )


async def get_user_purchases(user_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM purchases WHERE user_id = $1 ORDER BY id DESC", user_id
        )


# ---------- SIRLI SANDIQ ----------


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


async def is_item_owned(user_id: int, item_id: str) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM purchases WHERE user_id = $1 AND item_id = $2", user_id, item_id
        )
        return row is not None


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



# ---------- MOLIYAVIY VA UMUMIY STATISTIKA ----------

async def get_financial_stats():
    async with _pool.acquire() as conn:
        total_stars = await conn.fetchval(
            "SELECT COALESCE(SUM(amount_stars), 0) FROM star_payments WHERE status = 'pending' OR status IS NOT NULL"
        ) or 0
        total_payments = await conn.fetchval("SELECT COUNT(*) FROM star_payments") or 0
        pending_premium = await conn.fetchval(
            "SELECT COUNT(*) FROM premium_orders WHERE status = 'kutilmoqda'"
        ) or 0
        completed_premium = await conn.fetchval(
            "SELECT COUNT(*) FROM premium_orders WHERE status != 'kutilmoqda'"
        ) or 0
        return {
            "total_stars": total_stars,
            "total_payments": total_payments,
            "pending_premium": pending_premium,
            "completed_premium": completed_premium,
        }


async def get_usage_stats():
    async with _pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users") or 0
        new_today = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE joined_date::date = CURRENT_DATE"
        ) or 0
        new_week = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE joined_date::date >= CURRENT_DATE - INTERVAL '7 days'"
        ) or 0
        new_month = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE joined_date::date >= CURRENT_DATE - INTERVAL '30 days'"
        ) or 0
        active_today = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE last_active_date::date = CURRENT_DATE"
        ) or 0
        return {
            "total_users": total_users,
            "new_today": new_today,
            "new_week": new_week,
            "new_month": new_month,
            "active_today": active_today,
        }

# ---------- REFERAL IP-CHEKLOVI (firibgarlikka qarshi) ----------

async def set_signup_ip_if_missing(user_id: int, ip: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET signup_ip = $2 WHERE user_id = $1 AND signup_ip IS NULL",
            user_id, ip,
        )


async def count_rewarded_referrals_with_ip(ip: str) -> int:
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE signup_ip = $1 AND referral_rewarded = TRUE",
            ip,
        ) or 0

async def update_shop_product_price(product_id: int, cost_usd: float, cost_coins: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE shop_products SET cost_usd = $2, cost = $3 WHERE id = $1",
            product_id, cost_usd, cost_coins,
        )


async def delete_shop_product(product_id: int):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE shop_products SET active = FALSE WHERE id = $1", product_id)

# ---------- ONBOARDING VA FAOLLIK VAQTI (push shaxsiylashtirish) ----------

async def mark_onboarding_seen(user_id: int):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE users SET has_seen_onboarding = TRUE WHERE user_id = $1", user_id)


async def record_active_hour(user_id: int, hour: int):
    """Foydalanuvchi qaysi soatda faol ekanini saqlaydi (push-eslatmani
    shu vaqtga moslashtirish uchun). Oxirgi faol soat saqlanadi."""
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE users SET active_hour = $2 WHERE user_id = $1", user_id, hour)


async def get_users_active_at_hour(hour: int):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT user_id, username FROM users WHERE active_hour = $1 AND last_active_date IS NOT NULL",
            hour,
        )

# ================== CHALLENGE TIZIMI ==================

async def create_challenge(book_title: str, cover_path: str, description: str,
                            days_count: int, pass_percent: int = 80) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO challenges (book_title, cover_path, description, days_count, pass_percent, status, created_at)
               VALUES ($1, $2, $3, $4, $5, 'active', $6) RETURNING id""",
            book_title, cover_path, description, days_count, pass_percent,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        return row["id"]


async def add_challenge_day(challenge_id: int, day_number: int, reading_text: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO challenge_days (challenge_id, day_number, reading_text)
               VALUES ($1, $2, $3)
               ON CONFLICT (challenge_id, day_number) DO UPDATE SET reading_text = $3""",
            challenge_id, day_number, reading_text,
        )


async def add_challenge_questions(challenge_id: int, day_number, questions: list):
    """day_number = None bo'lsa, bu FINAL test savollari hisoblanadi."""
    async with _pool.acquire() as conn:
        for q in questions:
            await conn.execute(
                """INSERT INTO challenge_questions
                   (challenge_id, day_number, question, option_a, option_b, option_c, option_d, correct_option)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                challenge_id, day_number, q["question"], q["a"], q["b"], q["c"], q["d"], q["correct"],
            )


async def set_challenge_top_bonuses(challenge_id: int, bonuses: list):
    async with _pool.acquire() as conn:
        await conn.execute("DELETE FROM challenge_top_bonuses WHERE challenge_id = $1", challenge_id)
        for b in bonuses:
            await conn.execute(
                """INSERT INTO challenge_top_bonuses (challenge_id, rank, coin_reward, xp_reward)
                   VALUES ($1, $2, $3, $4)""",
                challenge_id, b["rank"], b["coin"], b["xp"],
            )


async def get_challenge_top_bonuses(challenge_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM challenge_top_bonuses WHERE challenge_id = $1 ORDER BY rank ASC", challenge_id
        )


async def get_active_challenge():
    async with _pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM challenges WHERE status = 'active' ORDER BY id DESC LIMIT 1"
        )


async def get_challenge(challenge_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM challenges WHERE id = $1", challenge_id)


async def get_challenge_day(challenge_id: int, day_number: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM challenge_days WHERE challenge_id = $1 AND day_number = $2",
            challenge_id, day_number,
        )


async def get_challenge_questions(challenge_id: int, day_number):
    async with _pool.acquire() as conn:
        if day_number is None:
            return await conn.fetch(
                "SELECT * FROM challenge_questions WHERE challenge_id = $1 AND day_number IS NULL",
                challenge_id,
            )
        return await conn.fetch(
            "SELECT * FROM challenge_questions WHERE challenge_id = $1 AND day_number = $2",
            challenge_id, day_number,
        )


async def join_challenge(challenge_id: int, user_id: int):
    async with _pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM challenge_participants WHERE challenge_id = $1 AND user_id = $2",
            challenge_id, user_id,
        )
        if existing:
            return existing
        row = await conn.fetchrow(
            """INSERT INTO challenge_participants (challenge_id, user_id, joined_at, current_day, status)
               VALUES ($1, $2, $3, 1, 'in_progress') RETURNING *""",
            challenge_id, user_id, datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        return row


async def get_participant(challenge_id: int, user_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM challenge_participants WHERE challenge_id = $1 AND user_id = $2",
            challenge_id, user_id,
        )


async def get_participant_by_id(participant_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM challenge_participants WHERE id = $1", participant_id)


async def get_daily_progress(participant_id: int, day_number: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM challenge_daily_progress WHERE participant_id = $1 AND day_number = $2",
            participant_id, day_number,
        )


async def record_daily_progress(participant_id: int, day_number: int, correct: int, total: int,
                                 coins_earned: int, xp_earned: int, new_streak: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO challenge_daily_progress (participant_id, day_number, completed_at, correct_count, total_count)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (participant_id, day_number) DO NOTHING""",
            participant_id, day_number, datetime.now().strftime("%Y-%m-%d %H:%M"), correct, total,
        )
        await conn.execute(
            """UPDATE challenge_participants
               SET current_day = current_day + 1, streak = $2,
                   total_coins = total_coins + $3, total_xp = total_xp + $4
               WHERE id = $1""",
            participant_id, new_streak, coins_earned, xp_earned,
        )


async def record_final_result(participant_id: int, correct: int, total: int, percent: float,
                               coins_earned: int, xp_earned: int, status: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            """UPDATE challenge_participants
               SET final_correct = $2, final_total = $3, final_percent = $4,
                   total_coins = total_coins + $5, total_xp = total_xp + $6,
                   completed_at = $7, status = $8
               WHERE id = $1""",
            participant_id, correct, total, percent, coins_earned, xp_earned,
            datetime.now().strftime("%Y-%m-%d %H:%M"), status,
        )


async def get_challenge_leaderboard(challenge_id: int, limit: int = 100):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            """SELECT cp.*, u.username FROM challenge_participants cp
               JOIN users u ON u.user_id = cp.user_id
               WHERE cp.challenge_id = $1 AND cp.status = 'completed'
               ORDER BY cp.final_percent DESC, cp.total_xp DESC, cp.completed_at ASC
               LIMIT $2""",
            challenge_id, limit,
        )


async def finish_challenge(challenge_id: int):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE challenges SET status = 'finished' WHERE id = $1", challenge_id)


async def delete_challenge(challenge_id: int) -> bool:
    """Challenge'ni va unga bog'liq BARCHA ma'lumotlarni (kunlar, savollar,
    ishtirokchilar, kunlik progress, top mukofotlar, sertifikat va nishonlar)
    butunlay o'chiradi. Challenge topilmasa False qaytaradi."""
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """DELETE FROM challenge_daily_progress
                   WHERE participant_id IN (
                       SELECT id FROM challenge_participants WHERE challenge_id = $1
                   )""",
                challenge_id,
            )
            await conn.execute("DELETE FROM challenge_participants WHERE challenge_id = $1", challenge_id)
            await conn.execute("DELETE FROM challenge_questions WHERE challenge_id = $1", challenge_id)
            await conn.execute("DELETE FROM challenge_days WHERE challenge_id = $1", challenge_id)
            await conn.execute("DELETE FROM challenge_top_bonuses WHERE challenge_id = $1", challenge_id)
            await conn.execute("DELETE FROM certificates WHERE challenge_id = $1", challenge_id)
            await conn.execute("DELETE FROM badges WHERE challenge_id = $1", challenge_id)
            result = await conn.execute("DELETE FROM challenges WHERE id = $1", challenge_id)
        # result masalan "DELETE 1" yoki "DELETE 0" ko'rinishida bo'ladi
        return result.endswith(" 0") is False


async def add_xp(user_id: int, amount: int) -> dict:
    """XP qo'shadi va levelni qayta hisoblaydi. Daraja ko'tarilganda mukofot
    berish uchun eski va yangi darajani ham qaytaradi."""
    async with _pool.acquire() as conn:
        before = await conn.fetchrow("SELECT xp FROM users WHERE user_id = $1", user_id)
        old_xp = before["xp"] if before and before["xp"] is not None else 0
        old_level = calculate_level_from_xp(old_xp)

        row = await conn.fetchrow(
            "UPDATE users SET xp = xp + $2 WHERE user_id = $1 RETURNING xp", user_id, amount
        )
        new_xp = row["xp"] if row else amount
        new_level = calculate_level_from_xp(new_xp)
        await conn.execute("UPDATE users SET level = $2 WHERE user_id = $1", user_id, new_level)
        return {"xp": new_xp, "level": new_level, "old_level": old_level}


LEVEL_XP_TABLE = [0, 100, 250, 500, 850, 1300, 1900, 2650, 3600, 4800]


def calculate_level_from_xp(xp: int) -> int:
    level = 1
    for i, threshold in enumerate(LEVEL_XP_TABLE):
        if xp >= threshold:
            level = i + 1
    return level


async def create_certificate(user_id: int, challenge_id: int, book_title: str, percent: float) -> str:
    code = f"KZ-{datetime.now().year}-{random.randint(10000, 99999)}"
    async with _pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO certificates (code, user_id, challenge_id, book_title, percent, issued_at)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            code, user_id, challenge_id, book_title, percent,
            datetime.now().strftime("%Y-%m-%d"),
        )
        return code


async def get_certificate_by_code(code: str):
    async with _pool.acquire() as conn:
        return await conn.fetchrow(
            """SELECT c.*, u.username FROM certificates c
               JOIN users u ON u.user_id = c.user_id
               WHERE c.code = $1""",
            code,
        )


async def add_badge(user_id: int, challenge_id: int, badge_name: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO badges (user_id, challenge_id, badge_name, issued_at)
               VALUES ($1, $2, $3, $4)""",
            user_id, challenge_id, badge_name, datetime.now().strftime("%Y-%m-%d"),
        )


async def get_user_badges(user_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM badges WHERE user_id = $1 ORDER BY id DESC", user_id)


async def has_badge_named(user_id: int, badge_name: str) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM badges WHERE user_id = $1 AND badge_name = $2 LIMIT 1",
            user_id, badge_name,
        )
        return row is not None


async def add_streak_freeze(user_id: int, amount: int = 1) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET streak_freezes = COALESCE(streak_freezes, 0) + $2 "
            "WHERE user_id = $1 RETURNING streak_freezes",
            user_id, amount,
        )
        return row["streak_freezes"] if row else amount


async def add_reading_seconds(user_id: int, seconds: int, target_seconds: int) -> dict:
    """Kunlik o'qish vaqtini (real, faol o'qish paytida) qo'shadi. Kun almashsa
    hisoblagich avtomatik nolga tushadi. Maqsadga yetganda (birinchi marta)
    newly_claimed=True qaytaradi — shunda chaqiruvchi tomon mukofot beradi."""
    today = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT daily_reading_seconds, daily_reading_date, daily_reading_claimed "
            "FROM users WHERE user_id = $1", user_id
        )
        if not row:
            return {"seconds_today": 0, "target_seconds": target_seconds, "claimed": False, "newly_claimed": False}

        current_seconds = row["daily_reading_seconds"] or 0
        current_date = row["daily_reading_date"]
        claimed = row["daily_reading_claimed"] or False

        if current_date != today:
            current_seconds = 0
            claimed = False

        new_seconds = current_seconds + max(0, seconds)
        newly_claimed = False
        if new_seconds >= target_seconds and not claimed:
            claimed = True
            newly_claimed = True

        await conn.execute(
            "UPDATE users SET daily_reading_seconds = $2, daily_reading_date = $3, "
            "daily_reading_claimed = $4 WHERE user_id = $1",
            user_id, min(new_seconds, target_seconds), today, claimed,
        )
        return {
            "seconds_today": min(new_seconds, target_seconds),
            "target_seconds": target_seconds,
            "claimed": claimed,
            "newly_claimed": newly_claimed,
        }


async def get_reading_progress_today(user_id: int, target_seconds: int) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT daily_reading_seconds, daily_reading_date, daily_reading_claimed "
            "FROM users WHERE user_id = $1", user_id
        )
        if not row or row["daily_reading_date"] != today:
            return {"seconds_today": 0, "target_seconds": target_seconds, "claimed": False}
        return {
            "seconds_today": min(row["daily_reading_seconds"] or 0, target_seconds),
            "target_seconds": target_seconds,
            "claimed": row["daily_reading_claimed"] or False,
        }


async def get_user_challenge_history(user_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            """SELECT cp.*, c.book_title FROM challenge_participants cp
               JOIN challenges c ON c.id = cp.challenge_id
               WHERE cp.user_id = $1 AND cp.status = 'completed'
               ORDER BY cp.completed_at DESC""",
            user_id,
        )


async def get_challenge_participants_for_finish(challenge_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM challenge_participants WHERE challenge_id = $1 AND status = 'completed'",
            challenge_id,
        )


async def get_challenge_participant_count(challenge_id: int) -> int:
    """Challenge'ga qo'shilgan BARCHA ishtirokchilar sonini qaytaradi (status'idan
    qat'i nazar — in_progress, completed, failed hammasi hisobga olinadi)."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM challenge_participants WHERE challenge_id = $1",
            challenge_id,
        )
        return row["cnt"] if row else 0


async def deactivate_task(task_id: int):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE tasks SET active = FALSE WHERE id = $1", task_id)


async def deactivate_quiz_set(quiz_set_id: int):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE quiz_sets SET active = FALSE WHERE id = $1", quiz_set_id)


async def deactivate_weekly_draw():
    async with _pool.acquire() as conn:
        result = await conn.execute("UPDATE weekly_draws SET active = FALSE WHERE active = TRUE")
        return result.endswith(" 0") is False


async def get_all_storage_paths() -> set:
    """/data/books ostidagi barcha jadvallarda hozir ISHLATILAYOTGAN fayl
    yo'llarini (cover_path, image_path, audio_path, avatar_path) bitta
    to'plamda qaytaradi. Diskni tozalashda (o'chirilgan/etim fayllarni
    aniqlash uchun) ishlatiladi — hech qaysi jadval e'tibordan chetda
    qolmasligi juda muhim, aks holda kerakli fayl xato o'chirilib ketishi
    mumkin."""
    used = set()
    async with _pool.acquire() as conn:
        queries = [
            "SELECT avatar_path AS p FROM users WHERE avatar_path IS NOT NULL",
            "SELECT cover_path AS p FROM books WHERE cover_path IS NOT NULL",
            "SELECT image_path AS p FROM book_pages WHERE image_path IS NOT NULL",
            "SELECT audio_path AS p FROM book_audio_chapters WHERE audio_path IS NOT NULL",
            "SELECT image_path AS p FROM weekly_draws WHERE image_path IS NOT NULL",
            "SELECT image_path AS p FROM shop_products WHERE image_path IS NOT NULL",
            "SELECT cover_path AS p FROM challenges WHERE cover_path IS NOT NULL",
        ]
        for q in queries:
            rows = await conn.fetch(q)
            for r in rows:
                if r["p"]:
                    used.add(r["p"].lstrip("/"))
    return used
