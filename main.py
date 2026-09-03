# main.py
# "Kitob o'qib koin yig'ish" - Telegram Mini App backend
# aiogram 3 (bot: majburiy obuna, referral) + aiohttp (Mini App uchun API server)
# Ma'lumotlar Postgres'da saqlanadi (Railway Database xizmati) - Volume shart emas

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
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

import database as db

# ================== SOZLAMALAR ==================

BOT_TOKEN = "8900492996:AAHH1IITD_HO7d5z_tCxZSQHlRkYnKLY5TY"

ADMIN_IDS = [8241010228]

FORCE_CHANNELS = [
    "@kitob_coin",
]

# Railway'dan olgan domeningiz (Settings -> Networking -> Generate Domain)
WEBAPP_URL = "https://web-production-aa006.up.railway.app"

PORT = int(os.environ.get("PORT", 8080))

COINS_PER_PAGE = 10
REFERRAL_BONUS = 150

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# ================== YORDAMCHI FUNKSIYALAR ==================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def check_subscription(user_id: int) -> list:
    not_subscribed = []
    for channel in FORCE_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ("left", "kicked"):
                not_subscribed.append(channel)
        except Exception as e:
            logger.warning(f"Kanal tekshirishda xato ({channel}): {e}")
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
    user = await db.get_user(user_id)
    if user and user["referred_by"] and not user["referral_rewarded"]:
        inviter_id = user["referred_by"]
        await db.add_coins(inviter_id, REFERRAL_BONUS)
        await db.mark_referral_rewarded(user_id)
        try:
            await bot.send_message(
                inviter_id,
                f"🎉 Sizning taklifingiz bilan yangi foydalanuvchi qo'shildi!\n"
                f"+{REFERRAL_BONUS} koin hisobingizga qo'shildi."
            )
        except Exception:
            pass


# ================== BOT HANDLERLARI ==================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    referred_by = None
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            ref_id = int(args[1].replace("ref_", ""))
            if ref_id != user_id:
                referred_by = ref_id
        except ValueError:
            pass

    is_new = await db.create_user_if_missing(user_id, username, referred_by)

    not_subscribed = await check_subscription(user_id)
    if not_subscribed:
        await message.answer(
            "👋 Xush kelibsiz!\n\n"
            "Ilovadan foydalanish uchun avval quyidagi kanallarga a'zo bo'ling, "
            "so'ngra <b>\"✅ Tekshirish\"</b> tugmasini bosing:",
            reply_markup=subscription_keyboard(not_subscribed),
        )
        return

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
    users = await db.get_all_users()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Tasodifiy g'olibni aniqlash", callback_data="admin_randomizer")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
    ])
    await message.answer(
        f"🛠 <b>Admin panel</b>\n\n👥 Jami foydalanuvchilar: {len(users)}",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data == "admin_randomizer")
async def callback_randomizer(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    users = await db.get_all_users()
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
    users = await db.get_all_users()
    total_coins = sum(u["coins"] for u in users)
    await callback.message.answer(
        f"📊 <b>Statistika</b>\n\n"
        f"👥 Foydalanuvchilar: {len(users)}\n"
        f"💰 Jami koinlar: {total_coins}"
    )
    await callback.answer()


# ================== MINI APP UCHUN initData TEKSHIRISH ==================

def validate_init_data(init_data: str):
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


def get_authenticated_user_id(request: web.Request, body: dict):
    init_data = body.get("initData") or request.headers.get("X-Init-Data")
    if not init_data:
        return None
    user = validate_init_data(init_data)
    if not user:
        return None
    return user.get("id")


# ================== MINI APP API (aiohttp) ==================

async def api_me(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    username = user_data.get("username") or user_data.get("first_name", "")
    await db.create_user_if_missing(user_id, username)
    user = await db.get_user(user_id)

    bot_info = await bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    return web.json_response({
        "user_id": user_id,
        "coins": user["coins"],
        "referral_link": referral_link,
    })


async def api_earn(request: web.Request):
    body = await request.json()
    user_id = get_authenticated_user_id(request, body)
    if not user_id:
        return web.json_response({"error": "unauthorized"}, status=401)

    amount = int(body.get("amount", 0))
    if amount <= 0 or amount > COINS_PER_PAGE:
        return web.json_response({"error": "invalid_amount"}, status=400)

    new_balance = await db.add_coins(user_id, amount)
    return web.json_response({"success": True, "coins": new_balance})


async def api_purchase(request: web.Request):
    body = await request.json()
    user_id = get_authenticated_user_id(request, body)
    if not user_id:
        return web.json_response({"error": "unauthorized"}, status=401)

    cost = int(body.get("cost", 0))
    user = await db.get_user(user_id)
    if not user or user["coins"] < cost:
        return web.json_response({"error": "insufficient_funds"}, status=400)

    new_balance = await db.add_coins(user_id, -cost)
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
    await db.init_db()
    logger.info("Ma'lumotlar bazasiga ulandi (Postgres)")

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

