# main.py
# "Kitob Ovi" - Telegram Mini App backend
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

APP_NAME = "Kitob Ovi"

# XAVFSIZLIK: Token kodda YOZILMAYDI — Railway'da Variables bo'limiga BOT_TOKEN qilib qo'shiladi.
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi! Railway loyihasida Variables bo'limiga "
        "BOT_TOKEN nomi bilan yangi tokeningizni qo'shing."
    )

ADMIN_IDS = [8241010228]

FORCE_CHANNELS = [
    "@kitob_coin",
]

# Railway'dan olgan domeningiz (Settings -> Networking -> Generate Domain)
WEBAPP_URL = "https://web-production-aa006.up.railway.app"

PORT = int(os.environ.get("PORT", 8080))

# ---------- Sahifa o'qish mukofoti ----------
COINS_PER_PAGE = 10
COINS_PER_PAGE_PREMIUM = 20          # Premium: 2 baravar

# ---------- Sahifa taymeri (sekund) — frontend shu qiymatni /api/me dan olib ishlatishi kerak ----------
PAGE_TIMER_SECONDS = 30
PAGE_TIMER_SECONDS_PREMIUM = 15

# ---------- Referral bonusi ----------
REFERRAL_BONUS = 300

# ---------- Referral MILESTONE (bosqichma-bosqich sovg'a) ----------
# Har REFERRAL_MILESTONE_STEP ta yangi taklif qilingan do'st uchun avtomatik Premium sovg'a
REFERRAL_MILESTONE_STEP = 5
REFERRAL_MILESTONE_PREMIUM_DAYS = 3

# ---------- Kunlik STREAK bonusi (hammaga, ketma-ket kirgan kunlar uchun ortib boradi) ----------
# Kun 1..7 uchun bonus, 7-kundan keyin yana 1-kundan boshlanadi (haftalik sikl)
STREAK_BONUS_TABLE = {1: 5, 2: 10, 3: 15, 4: 25, 5: 35, 6: 50, 7: 100}

# ---------- Tanlov (Quiz) uchun sozlamalar ----------
QUIZ_REWARD_PER_CORRECT = 15
QUIZ_REWARD_PER_CORRECT_PREMIUM = 22  # +50%
MAX_QUIZ_QUESTIONS = 30  # bitta tanlovda bo'lishi mumkin bo'lgan maksimal savol soni (himoya uchun)

# ---------- Do'kon chegirmasi ----------
SHOP_DISCOUNT_PREMIUM = 0.20         # 20%

# ---------- Kunlik bonus (faqat Premium, streak bonusidan TASHQARI qo'shimcha) ----------
DAILY_BONUS_PREMIUM = 50

# ---------- Premium muddati ----------
PREMIUM_DURATION_DAYS = 7

# ---------- Eslatma (reminder) sozlamalari ----------
REMINDER_CHECK_INTERVAL_SECONDS = 6 * 3600   # har 6 soatda tekshiradi
REMINDER_INACTIVE_DAYS = 1                    # 1 kun ochilmasa eslatma yuboriladi
REMINDER_MESSAGES = [
    "📖 Yangi bob sizni kutmoqda! Hoziroq o'qishni davom ettiring va koin yig'ing.",
    "🏹 Kitob Ovi sizni sog'indi! Bugun qancha koin yig'a olasiz?",
    "🔥 Streak seriyangizni uzmang — bugun kirib, bonusingizni oling!",
]

# Do'kon mahsulotlari — narx va turi FAQAT serverda aniqlanadi (mijoz o'zgartira olmasligi uchun)
# premium_only=True bo'lgan mahsulotni faqat hozir Premium bo'lgan foydalanuvchi sotib ola oladi.
SHOP_ITEMS = {
    "book1": {"name": "Yangi kitob: \"Yulduzlar sayohati\"", "emoji": "📗", "cost": 100, "type": "book", "premium_only": False},
    "book2": {"name": "Yangi kitob: \"Vaqt mashinasi\"", "emoji": "📘", "cost": 150, "type": "book", "premium_only": False},
    "badge": {"name": "Faxriy nishon (profilga)", "emoji": "🏅", "cost": 80, "type": "badge", "premium_only": False},
    "premium": {"name": "1 haftalik Premium a'zolik", "emoji": "⭐", "cost": 500, "type": "premium", "premium_only": False},

    # --- Asl, ilova uchun yozilgan matnlar; index.html'dagi Tanlov testlari shularga mos ---
    "book3": {"name": "Yangi kitob: \"Kumush tong\"", "emoji": "📙", "cost": 150, "type": "book", "premium_only": False},
    "book4": {"name": "Yangi kitob: \"Ikkilanish\"", "emoji": "📕", "cost": 150, "type": "book", "premium_only": False},
    "book5": {"name": "Yangi kitob: \"Ikki dunyo oralig'ida\"", "emoji": "🌟", "cost": 200, "type": "book", "premium_only": True},
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# ================== YORDAMCHI FUNKSIYALAR ==================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_streak_bonus_amount(streak: int) -> int:
    day_in_cycle = ((streak - 1) % 7) + 1
    return STREAK_BONUS_TABLE.get(day_in_cycle, 5)


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
        await check_referral_milestone(inviter_id)


async def check_referral_milestone(inviter_id: int):
    """Har REFERRAL_MILESTONE_STEP ta qabul qilingan referal uchun avtomatik Premium sovg'a beradi."""
    count = await db.get_referral_count(inviter_id)
    tier = count // REFERRAL_MILESTONE_STEP
    current_tier = await db.get_referral_milestone_tier(inviter_id)
    if tier > current_tier:
        await db.set_referral_milestone_tier(inviter_id, tier)
        expires = await db.activate_premium(inviter_id, days=REFERRAL_MILESTONE_PREMIUM_DAYS)
        try:
            await bot.send_message(
                inviter_id,
                f"🏆 Tabriklaymiz! Siz {count} ta do'st taklif qildingiz.\n"
                f"Sovg'a sifatida sizga {REFERRAL_MILESTONE_PREMIUM_DAYS} kunlik Premium taqdim etildi "
                f"(muddati: {expires.strftime('%Y-%m-%d')} gacha)!"
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
        f"✅ Xush kelibsiz {APP_NAME}'ga!\n\n"
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
        f"🛠 <b>Admin panel</b>\n\n👥 Jami foydalanuvchilar: {len(users)}\n\n"
        f"💰 Koin qo'shish uchun buyruqlar:\n"
        f"<code>/addcoins 500</code> — o'zingizga 500 koin qo'shish\n"
        f"<code>/addcoins 123456789 500</code> — boshqa foydalanuvchiga koin qo'shish\n"
        f"(manfiy son yozsangiz — koin ayiradi, masalan <code>/addcoins -50</code>)\n\n"
        f"⭐ Premium qo'lda berish uchun:\n"
        f"<code>/addpremium 123456789</code> — 7 kunlik Premium beradi",
        reply_markup=keyboard,
    )


@dp.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"🆔 Sizning Telegram ID: <code>{message.from_user.id}</code>")


@dp.message(Command("addcoins"))
async def cmd_addcoins(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat administratorlar uchun.")
        return

    parts = message.text.split()
    try:
        if len(parts) == 3:
            target_id = int(parts[1])
            amount = int(parts[2])
        elif len(parts) == 2:
            target_id = message.from_user.id
            amount = int(parts[1])
        else:
            raise ValueError
    except ValueError:
        await message.answer(
            "❗ Foydalanish:\n"
            "<code>/addcoins 500</code> — o'zingizga 500 koin qo'shish\n"
            "<code>/addcoins 123456789 500</code> — boshqa foydalanuvchiga koin qo'shish"
        )
        return

    new_balance = await db.add_coins(target_id, amount)
    await message.answer(
        f"✅ <code>{target_id}</code> foydalanuvchiga {amount:+d} koin. Yangi balans: {new_balance}"
    )
    if target_id != message.from_user.id and amount > 0:
        try:
            await bot.send_message(target_id, f"🎁 Sizga admin tomonidan {amount} koin qo'shildi!")
        except Exception:
            pass


@dp.message(Command("addpremium"))
async def cmd_addpremium(message: Message):
    """Admin qo'lda foydalanuvchiga Premium berishi uchun (masalan sovrin sifatida)."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat administratorlar uchun.")
        return

    parts = message.text.split()
    try:
        target_id = int(parts[1]) if len(parts) >= 2 else message.from_user.id
        days = int(parts[2]) if len(parts) >= 3 else PREMIUM_DURATION_DAYS
    except ValueError:
        await message.answer(
            "❗ Foydalanish:\n"
            "<code>/addpremium 123456789</code> — 7 kunlik Premium beradi\n"
            "<code>/addpremium 123456789 14</code> — 14 kunlik Premium beradi"
        )
        return

    expires = await db.activate_premium(target_id, days=days)
    await message.answer(
        f"✅ <code>{target_id}</code> foydalanuvchiga {days} kunlik Premium berildi.\n"
        f"Tugash sanasi: {expires.strftime('%Y-%m-%d %H:%M')}"
    )
    if target_id != message.from_user.id:
        try:
            await bot.send_message(
                target_id,
                f"🌟 Sizga {days} kunlik Premium a'zolik berildi!\n"
                f"Amal qilish muddati: {expires.strftime('%Y-%m-%d')} gacha."
            )
        except Exception:
            pass


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
    premium_count = sum(1 for u in users if u["is_premium"])
    await callback.message.answer(
        f"📊 <b>Statistika</b>\n\n"
        f"👥 Foydalanuvchilar: {len(users)}\n"
        f"💰 Jami koinlar: {total_coins}\n"
        f"⭐ Premium foydalanuvchilar: {premium_count}"
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

    streak = await db.update_streak(user_id)
    is_premium = await db.check_premium_status(user_id)

    # Kunlik STREAK bonusi - HAMMAGA, kuniga 1 marta, kun tartibiga qarab ortib boradi
    streak_bonus_amount = get_streak_bonus_amount(streak)
    streak_bonus_earned = await db.try_grant_streak_bonus(user_id, streak_bonus_amount)

    # Qo'shimcha kunlik bonus - faqat Premium, kuniga 1 marta
    daily_bonus_earned = await db.try_grant_daily_bonus(user_id, DAILY_BONUS_PREMIUM)

    user = await db.get_user(user_id)
    books_count = await db.count_purchases_by_type(user_id, "book")
    badges_count = await db.count_purchases_by_type(user_id, "badge")
    pages_read = user["pages_read"] or 0
    hours_read = round(pages_read * 30 / 3600, 1)  # har sahifa ~30 soniya

    referral_count = await db.get_referral_count(user_id)
    referral_next_milestone = ((referral_count // REFERRAL_MILESTONE_STEP) + 1) * REFERRAL_MILESTONE_STEP

    bot_info = await bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    return web.json_response({
        "user_id": user_id,
        "coins": user["coins"],
        "referral_link": referral_link,
        "referral_count": referral_count,
        "referral_next_milestone": referral_next_milestone,
        "referral_milestone_step": REFERRAL_MILESTONE_STEP,
        "referral_milestone_premium_days": REFERRAL_MILESTONE_PREMIUM_DAYS,
        "is_premium": is_premium,
        "premium_expires_at": user["premium_expires_at"].strftime("%Y-%m-%d %H:%M") if user["premium_expires_at"] else None,
        "page_timer_seconds": PAGE_TIMER_SECONDS_PREMIUM if is_premium else PAGE_TIMER_SECONDS,
        "coins_per_page": COINS_PER_PAGE_PREMIUM if is_premium else COINS_PER_PAGE,
        "streak_bonus_earned": streak_bonus_earned,
        "daily_bonus_earned": daily_bonus_earned,
        "stats": {
            "books": books_count,
            "hours": hours_read,
            "streak": streak,
            "badges": badges_count,
        },
    })


async def api_earn(request: web.Request):
    body = await request.json()
    user_id = get_authenticated_user_id(request, body)
    if not user_id:
        return web.json_response({"error": "unauthorized"}, status=401)

    is_premium = await db.check_premium_status(user_id)
    max_allowed = COINS_PER_PAGE_PREMIUM if is_premium else COINS_PER_PAGE

    amount = int(body.get("amount", 0))
    if amount <= 0 or amount > max_allowed:
        return web.json_response({"error": "invalid_amount"}, status=400)

    new_balance = await db.add_coins(user_id, amount)
    await db.increment_pages_read(user_id)
    return web.json_response({"success": True, "coins": new_balance, "is_premium": is_premium})


async def api_shop_items(request: web.Request):
    """Do'kon ro'yxatini REAL (chegirma qo'llangan) narxlar bilan qaytaradi."""
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    is_premium = await db.check_premium_status(user_id)

    items = []
    for item_id, item in SHOP_ITEMS.items():
        base_cost = item["cost"]
        cost = round(base_cost * (1 - SHOP_DISCOUNT_PREMIUM)) if is_premium else base_cost
        items.append({
            "item_id": item_id,
            "name": item["name"],
            "emoji": item["emoji"],
            "type": item["type"],
            "cost": cost,
            "base_cost": base_cost,
            "discounted": is_premium,
            "premium_only": item.get("premium_only", False),
            "locked": item.get("premium_only", False) and not is_premium,
            "owned": await db.is_item_owned(user_id, item_id) if item["type"] != "premium" else False,
        })

    return web.json_response({"items": items, "is_premium": is_premium})


async def api_purchase(request: web.Request):
    body = await request.json()
    user_id = get_authenticated_user_id(request, body)
    if not user_id:
        return web.json_response({"error": "unauthorized"}, status=401)

    item_id = body.get("item")
    item = SHOP_ITEMS.get(item_id)
    if not item:
        return web.json_response({"error": "invalid_item"}, status=400)

    is_premium = await db.check_premium_status(user_id)

    if item.get("premium_only") and not is_premium:
        return web.json_response({"error": "premium_required"}, status=403)

    # "premium" turidagi mahsulot qayta-qayta sotib olinishi mumkin (yangilash/cho'zish uchun),
    # boshqa mahsulotlar (kitob, nishon) esa faqat bir marta.
    if item["type"] != "premium":
        already_owned = await db.is_item_owned(user_id, item_id)
        if already_owned:
            return web.json_response({"error": "already_owned"}, status=400)

    base_cost = item["cost"]
    cost = round(base_cost * (1 - SHOP_DISCOUNT_PREMIUM)) if is_premium else base_cost

    user = await db.get_user(user_id)
    if not user or user["coins"] < cost:
        return web.json_response({"error": "insufficient_funds"}, status=400)

    new_balance = await db.add_coins(user_id, -cost)
    await db.add_purchase(user_id, item_id, item["name"], item["emoji"], item["type"])

    result = {"success": True, "coins": new_balance, "cost_paid": cost}

    if item["type"] == "premium":
        expires = await db.activate_premium(user_id, days=PREMIUM_DURATION_DAYS)
        result["is_premium"] = True
        result["premium_expires_at"] = expires.strftime("%Y-%m-%d %H:%M")

    return web.json_response(result)


async def api_my_items(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    purchases = await db.get_purchases(user_id)
    items = [
        {
            "item_id": p["item_id"],
            "name": p["item_name"],
            "emoji": p["item_emoji"],
            "type": p["item_type"],
            "date": p["purchased_at"],
        }
        for p in purchases
    ]
    return web.json_response({"items": items})


async def api_quiz_complete(request: web.Request):
    """Tanlov (quiz) yakunlanganda to'g'ri javoblar soniga qarab koin beradi."""
    body = await request.json()
    user_id = get_authenticated_user_id(request, body)
    if not user_id:
        return web.json_response({"error": "unauthorized"}, status=401)

    is_premium = await db.check_premium_status(user_id)
    reward_per_correct = QUIZ_REWARD_PER_CORRECT_PREMIUM if is_premium else QUIZ_REWARD_PER_CORRECT

    correct = int(body.get("correct", 0))
    # Himoya: mijozdan kelgan qiymatni cheklaymiz
    correct = max(0, min(correct, MAX_QUIZ_QUESTIONS))
    earned = correct * reward_per_correct

    new_balance = await db.add_coins(user_id, earned)
    return web.json_response({"success": True, "coins": new_balance, "earned": earned, "is_premium": is_premium})


async def api_leaderboard(request: web.Request):
    """Koin va referral bo'yicha top-10 ro'yxatini qaytaradi (Do'stlar bo'limidagi Reyting uchun)."""
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    coins_rows = await db.get_coins_leaderboard(10)
    referral_rows = await db.get_referral_leaderboard(10)

    def display_name(username, user_id):
        if username and not str(username).isdigit():
            return f"@{username}"
        return f"ID{str(user_id)[-4:]}"

    coins_list = [
        {"name": display_name(r["username"], r["user_id"]), "value": r["coins"]}
        for r in coins_rows
    ]
    referral_list = [
        {"name": display_name(r["username"], r["user_id"]), "value": r["cnt"]}
        for r in referral_rows
    ]

    return web.json_response({"coins": coins_list, "referrals": referral_list})


async def index_page(request: web.Request):
    return web.FileResponse("./index.html")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index_page)
    app.router.add_get("/api/me", api_me)
    app.router.add_post("/api/earn", api_earn)
    app.router.add_get("/api/shop_items", api_shop_items)
    app.router.add_post("/api/purchase", api_purchase)
    app.router.add_get("/api/my_items", api_my_items)
    app.router.add_post("/api/quiz_complete", api_quiz_complete)
    app.router.add_get("/api/leaderboard", api_leaderboard)
    return app


# ================== FOYDALANUVCHILARGA ESLATMA YUBORISH (retention) ==================

async def reminder_loop():
    """
    Muntazam ravishda uzoq vaqt kirmagan foydalanuvchilarga eslatma yuboradi.
    Bu ixtiyoriy eslatma - foydalanuvchi botni istalgan vaqt /stop yoki block qilishi mumkin,
    hech qanday texnik cheklov qo'yilmagan.
    """
    while True:
        try:
            users = await db.get_users_for_reminder(inactive_days=REMINDER_INACTIVE_DAYS)
            for u in users:
                try:
                    text = random.choice(REMINDER_MESSAGES)
                    await bot.send_message(u["user_id"], text, reply_markup=webapp_keyboard())
                    await db.mark_reminder_sent(u["user_id"])
                    await asyncio.sleep(0.05)  # flood-limitga tushmaslik uchun kichik pauza
                except Exception:
                    # Foydalanuvchi botni block qilgan yoki boshqa xato - o'tkazib yuboramiz
                    pass
        except Exception as e:
            logger.warning(f"Reminder loop xatosi: {e}")

        await asyncio.sleep(REMINDER_CHECK_INTERVAL_SECONDS)


# ================== ASOSIY FUNKSIYA ==================

async def main():
    await db.init_db()
    logger.info("Ma'lumotlar bazasiga ulandi (Postgres)")

    # Xavfsizlik: agar boshqa xizmat (masalan ManyBot) shu tokenga webhook o'rnatgan bo'lsa,
    # uni majburan o'chiramiz — aks holda bizning bot xabarlarni olmay qoladi.
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook tozalandi, polling rejimida ishga tushmoqda")

    web_app = create_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info(f"Web server {PORT}-portda ishga tushdi")

    asyncio.create_task(reminder_loop())
    logger.info("Eslatma (reminder) fon vazifasi ishga tushirildi")

    logger.info(f"{APP_NAME} boti ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

