# main.py
# "Kitob o'qib koin yig'ish" - Telegram Mini App backend
# aiogram 3 (bot: majburiy obuna, referral) + aiohttp (Mini App uchun API server)

import asyncio
import hashlib
import hmac
import json
import logging
import random
import sqlite3
from datetime import datetime
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ================== SOZLAMALAR ==================

BOT_TOKEN = "SIZNING_BOT_TOKENINGIZ_BU_YERGA"

# Admin(lar)ning Telegram ID raqami(lari)
ADMIN_IDS = [123456789]

# Majburiy obuna kanallari. Username formatida yozing (@ belgisi bilan).
# Bot shu kanallarga ADMIN sifatida qo'shilgan bo'lishi kerak, aks holda a'zolikni tekshira olmaydi.
FORCE_CHANNELS = [
    "@kanal_username1",
    "@kanal_username2",
]

# Mini App joylashgan manzil (index.html shu bot bilan bir serverda joylashadi,
# Railway sizga domen bergach shu yerga yozasiz, masalan: https://sizning-loyiha.up.railway.app)
WEBAPP_URL = "https://SIZNING-DOMENINGIZ.up.railway.app"

# Server qaysi portda ishlashi (Railway avtomatik PORT beradi)
import os
PORT = int(os.environ.get("PORT", 8080))

DB_NAME = "/data/app.db"  # Railway Volume ichida - hech qachon o'chmaydi

# Sahifa o'qish uchun beriladigan koin va vaqt
COINS_PER_PAGE = 10
# Do'stni taklif qilgani uchun beriladigan koin
REFERRAL_BONUS = 150

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# ================== MA'LUMOTLAR BAZASI ==================

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            coins INTEGER DEFAULT 0,
            referred_by INTEGER,
            referral_rewarded INTEGER DEFAULT 0,
            joined_date TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def create_user_if_missing(user_id: int, username: str, referred_by: int = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    exists = cur.fetchone()
    if not exists:
        cur.execute(
            "INSERT INTO users (user_id, username, coins, referred_by, joined_date) VALUES (?, ?, 0, ?, ?)",
            (user_id, username, referred_by, datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
        conn.commit()
    conn.close()
    return not exists  # True bo'lsa - yangi foydalanuvchi


def add_coins(user_id: int, amount: int) -> int:
    """Koin qo'shadi (yoki manfiy son bilan ayiradi) va yangi balansni qaytaradi."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    cur.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row["coins"] if row else 0


def mark_referral_rewarded(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET referral_rewarded = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_all_users():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users")
    rows = cur.fetchall()
    conn.close()
    return rows


# ================== YORDAMCHI FUNKSIYALAR ==================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def check_subscription(user_id: int) -> list:
    """Foydalanuvchi a'zo bo'lmagan kanallar ro'yxatini qaytaradi (bo'sh bo'lsa - hammasiga a'zo)."""
    not_subscribed = []
    for channel in FORCE_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ("left", "kicked"):
                not_subscribed.append(channel)
        except Exception as e:
            logger.warning(f"Kanal tekshirishda xato ({channel}): {e}")
            # Agar bot kanalga admin sifatida qo'shilmagan bo'lsa ham shu yerga tushadi
            not_subscribed.append(channel)
    return not_subscribed


def subscription_keyboard(not_subscribed: list) -> InlineKeyboardMarkup:
    buttons = []
    for channel in not_subscribed:
        channel_name = channel.lstrip("@")
        buttons.append([InlineKeyboardButton(text=f"📢 {channel_name}", url=f"https://t.me/{channel_name}")])
    buttons.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def webapp_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Ilovani ochish", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])


async def grant_referral_bonus_if_needed(user_id: int):
    """Agar bu foydalanuvchi kimningdir taklifi bilan kirgan bo'lsa va hali mukofot berilmagan bo'lsa,
    taklif qilgan odamga bonus beradi. Faqat foydalanuvchi kanallarga a'zo bo'lgandan keyin chaqiriladi."""
    user = get_user(user_id)
    if user and user["referred_by"] and not user["referral_rewarded"]:
        inviter_id = user["referred_by"]
        add_coins(inviter_id, REFERRAL_BONUS)
        mark_referral_rewarded(user_id)
        try:
            await bot.send_message(
                inviter_id,
                f"🎉 Sizning taklifingiz bilan yangi foydalanuvchi qo'shildi!\n"
                f"+{REFERRAL_BONUS} koin hisobingizga qo'shildi."
            )
        except Exception:
            pass  # foydalanuvchi botni bloklagan bo'lishi mumkin


# ================== BOT HANDLERLARI ==================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    # Referral parametrini o'qish: /start ref_123456789
    referred_by = None
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            ref_id = int(args[1].replace("ref_", ""))
            if ref_id != user_id:  # o'zini o'zi taklif qilolmaydi
                referred_by = ref_id
        except ValueError:
            pass

    is_new = create_user_if_missing(user_id, username, referred_by)

    not_subscribed = await check_subscription(user_id)
    if not_subscribed:
        await message.answer(
            "👋 Xush kelibsiz!\n\n"
            "Ilovadan foydalanish uchun avval quyidagi kanallarga a'zo bo'ling, "
            "so'ngra <b>\"✅ Tekshirish\"</b> tugmasini bosing:",
            reply_markup=subscription_keyboard(not_subscribed),
        )
        return

    # A'zo bo'lgan - agar referral orqali kirgan bo'lsa, bonusni beramiz
    if is_new:
        await grant_referral_bonus_if_needed(user_id)

    await message.answer(
        "✅ Xush kelibsiz!\n\n"
        "📚 Kitob o'qing, koin yig'ing, sovg'alarga almashtiring!\n\n"
        "Quyidagi tugma orqali ilovani oching:",
        reply_markup=webapp_keyboard(),
    )


@dp.callback_query(F.data == "check_sub")
async def callback_check_sub(callback: CallbackQuery):
    user_id = callback.from_user.id
    not_subscribed = await check_subscription(user_id)

    if not_subscribed:
        await callback.answer("❌ Siz hali barcha kanallarga a'zo bo'lmadingiz!", show_alert=True)
        return

    await grant_referral_bonus_if_needed(user_id)

    await callback.message.edit_text(
        "✅ Rahmat! Siz barcha kanallarga a'zo bo'ldingiz.\n\n"
        "📚 Kitob o'qing, koin yig'ing, sovg'alarga almashtiring!\n\n"
        "Quyidagi tugma orqali ilovani oching:",
        reply_markup=webapp_keyboard(),
    )
    await callback.answer("✅ Tasdiqlandi!")


# ================== ADMIN PANEL ==================

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    users_count = len(get_all_users())
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Tasodifiy g'olibni aniqlash", callback_data="admin_randomizer")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
    ])
    await message.answer(
        f"🛠 <b>Admin panel</b>\n\n👥 Jami foydalanuvchilar: {users_count}",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data == "admin_randomizer")
async def callback_randomizer(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    users = get_all_users()
    if not users:
        await callback.answer("Foydalanuvchilar topilmadi.", show_alert=True)
        return

    winner = random.choice(users)
    name = f"@{winner['username']}" if winner["username"] and not str(winner["username"]).isdigit() else winner["user_id"]
    await callback.message.answer(
        f"🎉 <b>G'olib aniqlandi!</b>\n\n"
        f"👤 Foydalanuvchi: {name}\n"
        f"🆔 ID: <code>{winner['user_id']}</code>\n"
        f"💰 Koinlari: {winner['coins']}"
    )
    await callback.answer("G'olib tanlandi!")


@dp.callback_query(F.data == "admin_stats")
async def callback_admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    users = get_all_users()
    total_coins = sum(u["coins"] for u in users)
    await callback.message.answer(
        f"📊 <b>Statistika</b>\n\n"
        f"👥 Foydalanuvchilar: {len(users)}\n"
        f"💰 Jami koinlar: {total_coins}"
    )
    await callback.answer()


# ================== MINI APP UCHUN initData TEKSHIRISH ==================

def validate_init_data(init_data: str) -> dict | None:
    """
    Telegram WebApp yuborgan initData'ni tekshiradi (rasmiy Telegram formulasi bo'yicha),
    soxta so'rovlarning oldini oladi. Muvaffaqiyatli bo'lsa foydalanuvchi ma'lumotini qaytaradi.
    """
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if computed_hash != received_hash:
            return None

        user_json = parsed.get("user")
        if not user_json:
            return None
        return json.loads(user_json)
    except Exception as e:
        logger.warning(f"initData tekshirishda xato: {e}")
        return None


def get_authenticated_user_id(request: web.Request, body: dict) -> int | None:
    """So'rovdan initData'ni topib, tekshirib, haqiqiy user_id qaytaradi."""
    init_data = body.get("initData") or request.headers.get("X-Init-Data")
    if not init_data:
        return None
    user = validate_init_data(init_data)
    if not user:
        return None
    return user.get("id")


# ================== MINI APP API (aiohttp) ==================

async def api_me(request: web.Request):
    """GET /api/me - joriy foydalanuvchi ma'lumotini qaytaradi."""
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    username = user_data.get("username") or user_data.get("first_name", "")
    create_user_if_missing(user_id, username)
    user = get_user(user_id)

    bot_info = await bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    return web.json_response({
        "user_id": user_id,
        "coins": user["coins"],
        "referral_link": referral_link,
    })


async def api_earn(request: web.Request):
    """POST /api/earn {initData, amount} - sahifa o'qigani uchun koin qo'shadi."""
    body = await request.json()
    user_id = get_authenticated_user_id(request, body)
    if not user_id:
        return web.json_response({"error": "unauthorized"}, status=401)

    amount = int(body.get("amount", 0))
    # Xavfsizlik: bir martada juda katta miqdor qo'shilishining oldini olamiz
    if amount <= 0 or amount > COINS_PER_PAGE:
        return web.json_response({"error": "invalid_amount"}, status=400)

    new_balance = add_coins(user_id, amount)
    return web.json_response({"success": True, "coins": new_balance})


async def api_purchase(request: web.Request):
    """POST /api/purchase {initData, cost} - do'kondan xarid, koin yetarli bo'lsa ayiradi."""
    body = await request.json()
    user_id = get_authenticated_user_id(request, body)
    if not user_id:
        return web.json_response({"error": "unauthorized"}, status=401)

    cost = int(body.get("cost", 0))
    user = get_user(user_id)
    if not user or user["coins"] < cost:
        return web.json_response({"error": "insufficient_funds"}, status=400)

    new_balance = add_coins(user_id, -cost)
    return web.json_response({"success": True, "coins": new_balance})


async def index_page(request: web.Request):
    return web.FileResponse("./index.html")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index_page)
    app.router.add_get("/api/me", api_me)
    app.router.add_post("/api/earn", api_earn)
    app.router.add_post("/api/purchase", api_purchase)
    return app


# ================== ASOSIY FUNKSIYA ==================

async def main():
    init_db()

    web_app = create_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info(f"Web server {PORT}-portda ishga tushdi")

    logger.info("Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

