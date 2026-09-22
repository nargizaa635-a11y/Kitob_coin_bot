# main.py
# "Kitobzor" - Telegram Mini App backend
# aiogram 3 + aiohttp

import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import random
import re
import shutil
from urllib.parse import parse_qsl, quote
from datetime import datetime, timedelta

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    LabeledPrice,
    PreCheckoutQuery,
    SuccessfulPayment,
    FSInputFile,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import database as db
from pdf_tools import convert_pdf_hybrid

# ================== SOZLAMALAR ==================

APP_NAME = "Kitobzor"

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi! Railway loyihasida Variables bo'limiga "
        "BOT_TOKEN nomi bilan yangi tokeningizni qo'shing."
    )

ADMIN_IDS = [8241010228, 1920583847, 8774070929]

# Bosh adminlar — faqat ular /addcoins, /addrefs, /addpremium, /moliya kabi "xavfli"
# (real qiymat/pul bilan bog'liq) buyruqlarni ishlata oladi. Yangi qo'shiladigan oddiy
# adminlar (masalan kontent boshqaruvchilar) bu ro'yxatga kiritilmaydi.
SUPER_ADMIN_IDS = [8241010228, 1920583847, 8774070929]

FORCE_CHANNELS = [
    "@Kitobzor_loyihasi",
]

WEBAPP_URL = "https://web-production-aa006.up.railway.app"
PORT = int(os.environ.get("PORT", 8080))

MILESTONE_PAGES = 25
MILESTONE_BONUS = 55
MILESTONE_BONUS_PREMIUM = 77

# Audio kitoblar: har tugatilgan bob uchun mukofot
AUDIO_CHAPTER_COIN_BONUS = 40
AUDIO_CHAPTER_COIN_BONUS_PREMIUM = 56
AUDIO_CHAPTER_XP_BONUS = 8

REFERRAL_BONUS = 88
REFERRAL_WELCOME_BONUS = 44
DAILY_REFERRAL_LIMIT = 15
PENDING_REFERRALS_CHECK_SECONDS = 600
REFERRAL_MILESTONE_STEP = 5
REFERRAL_MILESTONE_PREMIUM_DAYS = 3

STREAK_BONUS_TABLE = {1: 5, 2: 10, 3: 15, 4: 25, 5: 35, 6: 50, 7: 100}

QUIZ_REWARD_PER_CORRECT = 3
QUIZ_XP_PER_CORRECT = 2
QUIZ_REWARD_PER_CORRECT_PREMIUM = 5
QUIZ_PREMIUM_BONUS_PER_CORRECT = 2  # premium a'zoga HAR BIR test uchun bazaviy narxga qo'shiladigan bonus
MAX_QUIZ_QUESTIONS = 30

# ================== STARS / PREMIUM / AVTOMATIK NARXLASH ==================
# Valyuta kursi: admin tannarxni $ da kiritadi, bot avtomatik Koinga aylantiradi
STARS_PER_USD = 50          # $1 = 50 Stars (foydalanuvchi tomondan taxminiy narx)
COINS_PER_STAR = 100        # 1 Star = 100 Koin
DEFAULT_MARGIN_PERCENT = 17  # admin foizni ko'rsatmasa shu qo'llaniladi (15-20% oralig'ida)

# Telegramning rasmiy giftPremiumSubscription narxlari (o'zgartirib bo'lmaydi)
PREMIUM_STARS_OFFICIAL = {3: 1000, 6: 1500, 12: 2500}
# Sizning foyda ulushingiz (har bir muddat uchun ustiga qo'shiladi)
PREMIUM_STARS_MARGIN = {3: 50, 6: 75, 12: 100}
# Foydalanuvchidan olinadigan yakuniy narx (rasmiy + marja)
PREMIUM_STARS_PRICE = {m: PREMIUM_STARS_OFFICIAL[m] + PREMIUM_STARS_MARGIN[m] for m in PREMIUM_STARS_OFFICIAL}

# 1 oylik Premium — ikki xil to'lov usuli bilan (avtomatlashtirib bo'lmaydi, admin qo'lda beradi)
PREMIUM_1MONTH_COIN_PRICE = 39999
PREMIUM_1MONTH_STARS_PRICE = 300


def calculate_coin_price(cost_usd: float, margin_percent: float = None) -> int:
    """Tannarx ($) asosida do'kon/kitob narxini Koinda avtomatik hisoblaydi.
    Formula: Koin = tannarx($) x STARS_PER_USD x COINS_PER_STAR x (1 + marja%)
    """
    if margin_percent is None:
        margin_percent = DEFAULT_MARGIN_PERCENT
    base = float(cost_usd) * STARS_PER_USD * COINS_PER_STAR
    final_price = base * (1 + margin_percent / 100)
    return int(round(final_price / 100) * 100)  # 100 ga yaxlitlash (chiroyli son uchun)

# Do'kondagi real mahsulotlar narxini so'mda ham ko'rsatish uchun (kurs o'zgarganda shu qatorni yangilang)
UZS_PER_USD = 12700


def usd_to_uzs(cost_usd: float) -> int:
    if not cost_usd:
        return 0
    raw = float(cost_usd) * UZS_PER_USD
    return int(round(raw / 500) * 500)  # 500 so'mga yaxlitlash

SHOP_DISCOUNT_PREMIUM = 0.17
BOOK_COIN_DISCOUNT_PREMIUM = 0.17

# Do'kon xaridlari uchun bonus: har 3-xariddan keyin shuncha koin qo'shiladi
SHOP_LOYALTY_EVERY_N = 3
SHOP_LOYALTY_BONUS_COINS = 333


async def apply_shop_loyalty_bonus(user_id: int) -> int:
    """Yangi xariddan keyin chaqiriladi. Agar xaridlar soni 3ga bo'linsa, bonus koin qo'shadi.
    Bonus miqdorini (0 bo'lsa bonus yo'q) qaytaradi."""
    count = await db.get_user_purchase_count(user_id)
    if count > 0 and count % SHOP_LOYALTY_EVERY_N == 0:
        await db.add_coins(user_id, SHOP_LOYALTY_BONUS_COINS)
        return SHOP_LOYALTY_BONUS_COINS
    return 0


# ---------- XP / DARAJA (LEVEL) TIZIMI ----------

XP_PER_25_PAGES = 10       # 25 sahifa o'qilganda (50 koin ustiga)
XP_PER_DAILY_TASK = 20     # kunlik vazifa bajarilganda
XP_PER_REFERRAL = 15       # har referral uchun

# Kunlik o'qish vaqti vazifasi ("Kunlik vazifa" — real, faol o'qish paytida hisoblanadi)
DAILY_READING_TARGET_SECONDS = 600     # 10 daqiqa
DAILY_READING_PING_SECONDS = 20        # har ping'da server QATIY shuncha soniya qo'shadi (mijozga ishonilmaydi)
DAILY_READING_BONUS_COINS = 50
DAILY_READING_BONUS_XP = 20

# Daraja ko'tarilganda beriladigan maxsus nishonlar (bir martalik, shu darajaga yetganda)
LEVEL_BADGES = {
    3: "🥉 Varaq Homiysi",
    6: "🥈 Kutubxona Faxriysi",
    10: "🥇 Ma'rifat Donishmandi",
}


async def award_xp_and_level_rewards(user_id: int, xp_amount: int, notify: bool = True) -> dict:
    """XP qo'shadi; daraja ko'tarilsa, har bir yangi daraja uchun (daraja x 50) koin va,
    agar 3/6/10-darajaga yetilgan bo'lsa, maxsus nishon beradi. Foydalanuvchiga xabar yuboradi."""
    xp_info = await db.add_xp(user_id, xp_amount)
    old_level = xp_info.get("old_level", xp_info["level"])
    new_level = xp_info["level"]

    level_up_coins = 0
    new_badges = []
    if new_level > old_level:
        for lvl in range(old_level + 1, new_level + 1):
            level_up_coins += lvl * 50
            badge_name = LEVEL_BADGES.get(lvl)
            if badge_name and not await db.has_badge_named(user_id, badge_name):
                await db.add_badge(user_id, None, badge_name)
                new_badges.append(badge_name)
        if level_up_coins:
            await db.add_coins(user_id, level_up_coins)
        if notify:
            try:
                badge_line = ("\n🏅 Yangi nishon: " + ", ".join(new_badges)) if new_badges else ""
                await bot.send_message(
                    user_id,
                    f"🎉 Tabriklaymiz! Siz <b>{new_level}-levelga</b> ko'tarildingiz!\n"
                    f"🪙 +{level_up_coins} koin mukofot sifatida berildi.{badge_line}"
                )
            except Exception:
                pass

    xp_info["level_up_coins"] = level_up_coins
    xp_info["new_badges"] = new_badges
    return xp_info
DAILY_BONUS_PREMIUM = 50
PREMIUM_DURATION_DAYS = 7

FREE_DAILY_BONUS_AMOUNT = 20

DEFAULT_TANLOV_GOAL = 5
DEFAULT_TANLOV_DAYS = 20
DEFAULT_TANLOV_REWARD_TEXT = "500 koin sovg'a"

REMINDER_CHECK_INTERVAL_SECONDS = 3600  # har soat tekshiradi — shaxsiy faol vaqtga moslashtirish uchun
REMINDER_INACTIVE_DAYS = 1
REMINDER_MESSAGES = [
    "📖 Yangi bob sizni kutmoqda! Hoziroq o'qishni davom ettiring va koin yig'ing.",
    "🏹 Kitobzor sizni sog'indi! Bugun qancha koin yig'a olasiz?",
    "🔥 Streak seriyangizni uzmang — bugun kirib, bonusingizni oling!",
]

SHOP_ITEMS = {
    "badge": {"name": "Faxriy nishon (profilga)", "emoji": "🏅", "cost": 80, "type": "badge", "premium_only": False},
}

# Premium a'zolik darajalari — cheksiz qayta sotib olinadi (faqat premium tugagach).
# Har birida narxga alohida DOIMIY chegirma bor (muddat uzunroq bo'lsa, chegirma kattaroq).
PREMIUM_TIERS = {
    "varaq": {"name": "Varaq", "emoji": "📄", "days": 7, "base_cost": 500, "discount": 0.05},
    "bob": {"name": "Bob", "emoji": "📑", "days": 15, "base_cost": 950, "discount": 0.08},
    "jild": {"name": "Jild", "emoji": "📚", "days": 30, "base_cost": 1700, "discount": 0.10},
}


def premium_tier_final_cost(tier_id: str) -> int:
    tier = PREMIUM_TIERS[tier_id]
    return int(round(tier["base_cost"] * (1 - tier["discount"])))

CHESTS = {
    "oddiiy": {
        "name": "Oddiy sandiq",
        "emoji": "📦",
        "cost": 40,
        "daily_limit": 5,
        "rewards": [
            {"type": "coins", "min": 15, "max": 70, "chance": 100},
        ]
    },
    "oltin": {
        "name": "Oltin sandiq",
        "emoji": "🥇",
        "cost": 120,
        "daily_limit": 3,
        "rewards": [
            {"type": "coins", "min": 80, "max": 200, "chance": 70},
            {"type": "premium", "days": 1, "chance": 30},
        ]
    },
    "legend": {
        "name": "Legend sandiq",
        "emoji": "👑",
        "cost": 250,
        "daily_limit": 2,
        "rewards": [
            {"type": "coins", "min": 150, "max": 400, "chance": 50},
            {"type": "premium", "days": 3, "chance": 50},
        ]
    },
}

# Kitob fayllari qayerda saqlanadi (Railway Volume shu yerga ulangan bo'lishi kerak: /data)
BOOK_STORAGE_DIR = "/data/books"
BOOK_TMP_DIR = "/tmp/book_uploads"

# /start bosilganda yuboriladigan tanishtiruv animatsiyasi (GIF yoki qisqa MP4 video).
# Faylni shu papkaga "kitobzor_intro.gif" yoki "kitobzor_intro.mp4" nomi bilan qo'ying
# (loyihaning asosiy papkasidagi "assets/" ichiga). Fayl topilmasa, xatoga chiqmasdan
# oddiy matnli xabar bilan davom etadi.
INTRO_ANIMATION_PATH = "assets/kitobzor_intro.mp4"
if not os.path.exists(INTRO_ANIMATION_PATH):
    INTRO_ANIMATION_PATH = "assets/kitobzor_intro.gif"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


# ================== ADMIN: KITOB QO'SHISH (FSM) ==================

class AddBookStates(StatesGroup):
    title = State()
    author = State()
    cover = State()
    genre = State()
    pdf = State()
    referrals = State()
    coins = State()


class AddAudioBookStates(StatesGroup):
    title = State()
    author = State()
    cover = State()
    genre = State()
    chapters = State()
    referrals = State()
    coins = State()


class AddTaskStates(StatesGroup):
    title = State()
    task_type = State()
    goal = State()
    reward = State()


TASK_TYPE_LABELS = {
    "pages_read": "📖 Sahifa o'qish",
    "referrals": "👥 Do'st taklif qilish",
    "streak_days": "🔥 Ketma-ket kunlar",
    "quiz_correct": "🧠 Testda to'g'ri javob",
}


class SetDrawStates(StatesGroup):
    goal = State()
    days = State()
    channels = State()
    prizes = State()
    summary = State()
    image = State()


class SetAvatarStates(StatesGroup):
    waiting_photo = State()


class AddShopProductStates(StatesGroup):
    name = State()
    description = State()
    category = State()
    payment_type = State()
    image = State()
    cost_usd = State()
    price_uzs = State()
    referrals = State()
    stock = State()


SHOP_CATEGORY_LABELS = {
    "telegram_premium": "📱 Telegram Premium",
    "telegram_gift": "🎁 Telegram Gift",
    "stationery": "✏️ Kantselyariya",
    "book": "📚 Kitob",
    "other": "🏅 Boshqa",
}


class AddQuizStates(StatesGroup):
    title = State()
    referrals = State()
    file = State()


class ChallengeCreateStates(StatesGroup):
    cover = State()
    file = State()


# ================== YORDAMCHI FUNKSIYALAR ==================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS


def get_streak_bonus_amount(streak: int) -> int:
    day_in_cycle = ((streak - 1) % 7) + 1
    return STREAK_BONUS_TABLE.get(day_in_cycle, 5)


async def check_subscription(user_id: int, channels: list = None) -> list:
    if channels is None:
        channels = await db.get_active_force_channels()
    not_subscribed = []
    for channel in channels:
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


def get_webapp_url() -> str:
    """Telegram WebView keshini chetlab o'tish uchun URL'ga har safar yangi versiya
    belgisini qo'shadi. Shu tufayli Telegram index.html'ni har doim serverdan
    qayta yuklaydi, eski (keshdagi) dizaynni ko'rsatmaydi."""
    return f"{WEBAPP_URL}?v={int(datetime.utcnow().timestamp())}"


def webapp_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Ilovani ochish", web_app=WebAppInfo(url=get_webapp_url()))]
    ])


async def grant_referral_bonus_if_needed(user_id: int):
    """Referal bonusini beradi — FAQAT quyidagi shartlar bajarilganda:
    1) Taklif qilingan odam hali bonus olmagan bo'lishi kerak
    2) Taklif qilingan odam telefon raqamini ulagan bo'lishi kerak (soxta akkauntlarga qarshi)
    3) Taklif qiluvchining bugungi kunlik limiti (DAILY_REFERRAL_LIMIT) to'lmagan bo'lishi kerak —
       agar limit to'lgan bo'lsa, bonus keyinroq (process_pending_referrals orqali, ertasi kuni
       navbat bo'shagach) beriladi, umuman yo'qolmaydi."""
    user = await db.get_user(user_id)
    if not (user and user["referred_by"] and not user["referral_rewarded"]):
        return
    if not user["phone"]:
        return

    inviter_id = user["referred_by"]

    # IP-cheklov: agar shu IP'dan allaqachon referal bonusi berilgan bo'lsa, firibgarlik ehtimoli
    if user["signup_ip"]:
        ip_count = await db.count_rewarded_referrals_with_ip(user["signup_ip"])
        if ip_count > 0:
            await db.mark_referral_rewarded(user_id, datetime.now().strftime("%Y-%m-%d"))
            logger.warning(f"Referral fraud shubhasi: user {user_id}, IP {user['signup_ip']}")
            return

    today = datetime.now().strftime("%Y-%m-%d")
    today_count = await db.get_referral_rewards_today_count(inviter_id, today)
    if today_count >= DAILY_REFERRAL_LIMIT:
        return

    await db.add_coins(inviter_id, REFERRAL_BONUS)
    await db.add_coins(user_id, REFERRAL_WELCOME_BONUS)
    await db.mark_referral_rewarded(user_id, today)
    await award_xp_and_level_rewards(inviter_id, XP_PER_REFERRAL, notify=True)
    try:
        await bot.send_message(
            inviter_id,
            f"🎉 Sizning taklifingiz bilan yangi foydalanuvchi qo'shildi!\n"
            f"+{REFERRAL_BONUS} koin hisobingizga qo'shildi."
        )
    except Exception:
        pass
    try:
        await bot.send_message(
            user_id,
            f"🎁 Xush kelibsiz! Referal havola orqali qo'shilganingiz uchun +{REFERRAL_WELCOME_BONUS} koin sovg'a!"
        )
    except Exception:
        pass
    await check_referral_milestone(inviter_id)


async def process_pending_referrals():
    """Kunlik limit tufayli kechiktirilgan (yoki boshqa sabab bilan hali
    berilmagan) referal bonuslarini, har bir taklif qiluvchining kunlik
    limitini hisobga olgan holda, navbat bilan beradi."""
    pending = await db.get_pending_referrals(limit=200)
    if not pending:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    inviter_counts_cache = {}

    for row in pending:
        referred_id = row["user_id"]
        inviter_id = row["referred_by"]

        if inviter_id not in inviter_counts_cache:
            inviter_counts_cache[inviter_id] = await db.get_referral_rewards_today_count(inviter_id, today)

        if inviter_counts_cache[inviter_id] >= DAILY_REFERRAL_LIMIT:
            continue

        await db.add_coins(inviter_id, REFERRAL_BONUS)
        await db.mark_referral_rewarded(referred_id, today)
        inviter_counts_cache[inviter_id] += 1
        await award_xp_and_level_rewards(inviter_id, XP_PER_REFERRAL, notify=True)

        try:
            await bot.send_message(
                inviter_id,
                f"🎉 Sizning taklifingiz bilan yangi foydalanuvchi qo'shildi!\n"
                f"+{REFERRAL_BONUS} koin hisobingizga qo'shildi."
            )
        except Exception:
            pass
        await check_referral_milestone(inviter_id)


async def process_pending_referrals_loop():
    while True:
        try:
            await process_pending_referrals()
        except Exception as e:
            logger.warning(f"Pending referrals qayta ishlashda xato: {e}")
        await asyncio.sleep(PENDING_REFERRALS_CHECK_SECONDS)


async def check_referral_milestone(inviter_id: int):
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


def validate_init_data(init_data: str) -> dict | None:
    try:
        parsed = dict(parse_qsl(init_data))
        if "hash" not in parsed:
            return None
        received_hash = parsed.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calculated_hash != received_hash:
            return None
        user = json.loads(parsed.get("user", "{}"))
        return user
    except Exception:
        return None


# ================== BOT HANDLERLARI ==================

async def send_intro_animation(chat_id: int):
    """Botga kirilganda 'Kitobzor' tanishtiruv animatsiyasini yuboradi.
    Fayl topilmasa yoki yuborishda xatolik bo'lsa, jim o'tkazib yuboriladi —
    bu asosiy /start oqimini hech qachon to'xtatmasligi kerak."""
    try:
        if os.path.exists(INTRO_ANIMATION_PATH):
            animation = FSInputFile(INTRO_ANIMATION_PATH)
            if INTRO_ANIMATION_PATH.endswith(".mp4"):
                await bot.send_video(chat_id, animation)
            else:
                await bot.send_animation(chat_id, animation)
    except Exception:
        logger.exception("Intro animatsiyasini yuborishda xatolik")


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    await send_intro_animation(message.chat.id)

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

    referral_note = ""
    user = await db.get_user(user_id)
    if user and user["referred_by"] and not user["referral_rewarded"]:
        referral_note = (
            "\n\n📱 Sizni taklif qilgan do'stingizga bonus berilishi uchun, "
            "ilova ichida <b>Profil</b> bo'limidan telefon raqamingizni ulang."
        )

    await message.answer(
    f"🚀 <b>KITOBZOR'GA XUSH KELIBSIZ!</b>\n\n"
    "📚 <b>O‘qing.</b> Bilim oling.\n"
    "🪙 <b>Coin yig‘ing.</b> Mukofotlarga ega bo‘ling.\n\n"
    "🎯 Quizlar va topshiriqlar\n"
    "🔥 Kunlik streak bonuslar\n"
    "👥 Referal mukofotlar\n"
    "🎁 Sovg‘alar\n"
    "👑 Premium imkoniyatlar\n\n"
    "✨ <i>Kitobxonlik sayohatingiz shu yerdan boshlanadi.</i>\n\n"
    "👇 <b>Ilovani oching va boshlang!</b>",
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

    referral_note = ""
    user = await db.get_user(user_id)
    if user and user["referred_by"] and not user["referral_rewarded"]:
        referral_note = (
            "\n\n📱 Sizni taklif qilgan do'stingizga bonus berilishi uchun, "
            "ilova ichida <b>Profil</b> bo'limidan telefon raqamingizni ulang."
        )

    await callback.message.edit_text(
        "✅ Rahmat! Siz barcha kanallarga a'zo bo'ldingiz.\n\n"
        "📚 Kitob o'qing, koin yig'ing, sovg'alarga almashtiring!"
        f"{referral_note}\n\n"
        "Quyidagi tugma orqali ilovani oching:",
        reply_markup=webapp_keyboard(),
    )
    await callback.answer("✅ Tasdiqlandi!")


@dp.message(F.contact)
async def handle_shared_contact(message: Message):
    """Mini App ichidan Telegram.WebApp.requestContact() orqali yuborilgan
    telefon raqamini qabul qilib, bazaga saqlaydi."""
    contact = message.contact
    if not contact:
        return
    # Faqat foydalanuvchining O'ZINING kontakti qabul qilinadi (boshqa birovning
    # kontaktini yuborib, uni o'ziniki qilib bo'lmasin)
    if contact.user_id != message.from_user.id:
        return

    await db.set_user_phone(message.from_user.id, contact.phone_number)
    await message.answer("✅ Telefon raqamingiz saqlandi. Ilovaga qaytishingiz mumkin.")

    # Agar bu foydalanuvchi kimningdir taklifi bilan qo'shilgan bo'lsa,
    # telefon ulanishi bilan taklif qiluvchiga bonus berish shartlaridan
    # biri bajarildi — endi tekshirib, imkoni bo'lsa bonusni beramiz.
    await grant_referral_bonus_if_needed(message.from_user.id)


# ================== ADMIN PANEL ==================

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return

    users = await db.get_all_users()
    is_super = is_super_admin(message.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Tasodifiy g'olibni aniqlash", callback_data="admin_randomizer")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
    ])

    super_only_block = (
        f"💰 Koin qo'shish (faqat bosh admin):\n"
        f"<code>/addcoins 500</code> — o'zingizga\n"
        f"<code>/addcoins 123456789 500</code> — boshqa foydalanuvchiga\n\n"
        f"⭐ Premium berish (faqat bosh admin):\n"
        f"<code>/addpremium 123456789</code>\n\n"
        f"🧪 Sinov uchun o'zingizga referral qo'shish (faqat bosh admin):\n"
        f"<code>/addrefs son</code>\n\n"
        f"💰 Moliyaviy va statistik hisobotlar (faqat bosh admin):\n"
        f"<code>/moliya</code> / <code>/statistika</code>\n\n"
    ) if is_super else (
        f"ℹ️ Koin qo'shish, Premium berish, referral qo'shish va moliyaviy "
        f"hisobotlar faqat bosh administratorlarga ochiq.\n\n"
    )

    await message.answer(
        f"🛠 <b>Admin panel</b>{' (bosh admin)' if is_super else ''}\n\n👥 Jami foydalanuvchilar: {len(users)}\n\n"
        f"{super_only_block}"
        f"🖼 Bot avatarini o'zgartirish:\n"
        f"<code>/avatar</code> — rasm yuborishni so'raydi\n\n"
        f"🏆 Yangi tanlov tashkil qilish:\n"
        f"<code>/tanlov</code> — bot ketma-ket so'raydi (referral, kun, kanallar, sovg'alar, rasm)\n"
        f"<code>/tanlov_holati</code> — joriy tanlov holati\n"
        f"<code>/tanlov_ochir</code> — joriy tanlovni o'chirish\n\n"
        f"📚 Kitoblar:\n"
        f"<code>/addbook</code> — yangi kitob qo'shish (bot ketma-ket so'raydi)\n"
        f"<code>/audio_kitob_qosh</code> — yangi audio kitob qo'shish (bob-bob MP3 yuboriladi, /tayyor bilan yakunlanadi)\n"
        f"<code>/kitoblar_id</code> — barcha kitoblar ro'yxati (ID va nomi)\n"
        f"<code>/kitob_ochir ID</code> — kitobni o'chirish (audio kitob uchun ham shu)\n\n"
        f"✅ Vazifalar:\n"
        f"<code>/addtask</code> — yangi vazifa qo'shish (bot ketma-ket so'raydi)\n"
        f"<code>/vazifa_ochir ID</code> — vazifani o'chirish\n\n"
        f"🛍 Do'kon:\n"
        f"<code>/addshop</code> — yangi mahsulot qo'shish (bot ketma-ket so'raydi)\n"
        f"<code>/mahsulot_ochir ID</code> — mahsulotni o'chirish\n"
        f"<code>/narx_ozgartir ID narx</code> — narxni o'zgartirish\n"
        f"<code>/tahrirla_dokon</code> — to'liq ro'yxat va ko'rsatma\n"
        f"<code>/dokon_stok ID son</code> — zaxirani to'ldirish\n"
        f"<code>/buyurtmalar</code> — sotib olingan mahsulotlar ro'yxati\n\n"
        f"🧠 Test (quiz):\n"
        f"<code>/addquiz</code> — yangi test qo'shish (.txt fayl orqali)\n"
        f"<code>/test_ochir ID</code> — testni o'chirish\n\n"
        f"📢 Majburiy obuna kanallari:\n"
        f"<code>/kanal_qosh @kanal</code> / <code>/kanal_ochir @kanal</code> / <code>/kanallar</code>\n\n"
        f"⭐ Stars/Premium:\n"
        f"<code>/buy_premium</code> — foydalanuvchi ko'radigan buyruq\n"
        f"<code>/buy_coins</code> — Stars orqali koin sotib olish\n"
        f"<code>/complete_premium_ID</code> — 1 oylik buyurtmani yakunlash\n\n"
        f"📦 Sandiqlar:\n"
        f"<code>/sandiqlar</code> — faol sandiqlar ro'yxati\n"
        f"<code>/sandiq_qosh</code> — format ko'rsatmasi\n"
        f"<code>/sandiq_yarat id|nom|emoji|narx|kunlik_limit|mukofotlar</code> — yaratish/yangilash\n"
        f"<code>/sandiq_ochir id</code> — sandiqni o'chirish\n\n"
        f"📖 O'qish challenge (marafon):\n"
        f"<code>/challenge_yarat</code> — yangi marafon yaratish (.txt fayl orqali)\n"
        f"<code>/challenge_yakunlash [ID]</code> — marafonni yakunlab, mukofot tarqatish\n"
        f"<code>/challenge_ochir ID</code> — marafonni butunlay o'chirish\n\n"
        f"<code>/bekor</code> — istalgan jarayonni bekor qilish",
        reply_markup=keyboard,
    )


@dp.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"🆔 Sizning Telegram ID: <code>{message.from_user.id}</code>")


@dp.message(Command("avatar"))
async def cmd_set_avatar(message: Message, state: FSMContext):
    await state.set_state(SetAvatarStates.waiting_photo)
    await message.answer(
        "🖼 Profilingiz uchun rasm yuboring (rasm sifatida, hujjat emas).\n"
        "Bekor qilish uchun /bekor deb yozing."
    )


@dp.message(SetAvatarStates.waiting_photo, F.photo)
async def process_avatar_photo(message: Message, state: FSMContext):
    user_id = message.from_user.id
    avatar_dir = os.path.join(BOOK_STORAGE_DIR, "avatars")
    os.makedirs(avatar_dir, exist_ok=True)
    avatar_abs_path = os.path.join(avatar_dir, f"{user_id}.jpg")

    await bot.download(message.photo[-1], destination=avatar_abs_path)
    await db.set_user_avatar(user_id, f"avatars/{user_id}.jpg")
    await state.clear()

    await message.answer("✅ Profil rasmingiz saqlandi! Ilovaga qaytib ko'rishingiz mumkin.")


@dp.message(SetAvatarStates.waiting_photo)
async def process_avatar_invalid(message: Message):
    await message.answer("❗ Iltimos, rasm yuboring (📷), yoki /bekor bilan bekor qiling.")


@dp.message(Command("addcoins"))
async def cmd_addcoins(message: Message):
    if not is_super_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat bosh administratorlar uchun.")
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
            "<code>/addcoins 500</code>\n"
            "<code>/addcoins 123456789 500</code>"
        )
        return

    new_balance = await db.add_coins(target_id, amount)
    await message.answer(
        f"✅ <code>{target_id}</code> foydalanuvchiga {amount:+d} koin. Yangi balans: {new_balance}"
    )


@dp.message(Command("tozalash"))
async def cmd_tozalash(message: Message):
    """Diskni (Railway Volume /data/books) tozalash: DB'dagi hech qaysi
    jadvalda endi ishlatilmayotgan ("etim qolgan") fayllarni topadi.
    Xavfsizlik uchun IKKI BOSQICHLI:
      /tozalash          -> faqat hisobot (nechta fayl, qancha joy)
      /tozalash tasdiqla -> haqiqatan o'chiradi
    Shu bilan birga, uzoq vaqt (24 soatdan ortiq) qolib ketgan vaqtinchalik
    (/tmp/book_uploads) papkalarni ham tozalaydi — bular admin yuklash
    jarayoni tugallanmasdan (masalan xatolik yoki bekor qilish tufayli)
    qolib ketishi mumkin.
    """
    if not is_admin(message.from_user.id):
        return

    confirm = len(message.text.split()) > 1 and message.text.split()[1].lower() == "tasdiqla"

    await message.answer("🔍 Diskni skanerlayapman, biroz vaqt olishi mumkin...")

    used_paths = await db.get_all_storage_paths()

    orphan_files = []
    total_orphan_bytes = 0
    if os.path.isdir(BOOK_STORAGE_DIR):
        for root, _dirs, files in os.walk(BOOK_STORAGE_DIR):
            for fname in files:
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, BOOK_STORAGE_DIR).replace(os.sep, "/")
                if rel_path not in used_paths:
                    try:
                        size = os.path.getsize(full_path)
                    except OSError:
                        size = 0
                    orphan_files.append((full_path, rel_path, size))
                    total_orphan_bytes += size

    # /tmp/book_uploads ichidagi 24 soatdan eski, tugallanmagan vaqtinchalik papkalar
    stale_tmp_dirs = []
    total_tmp_bytes = 0
    if os.path.isdir(BOOK_TMP_DIR):
        cutoff = datetime.now().timestamp() - 24 * 3600
        for name in os.listdir(BOOK_TMP_DIR):
            full_path = os.path.join(BOOK_TMP_DIR, name)
            try:
                if os.path.getmtime(full_path) < cutoff:
                    size = sum(
                        os.path.getsize(os.path.join(dp_, f))
                        for dp_, _dn, fs in os.walk(full_path)
                        for f in fs
                    ) if os.path.isdir(full_path) else os.path.getsize(full_path)
                    stale_tmp_dirs.append(full_path)
                    total_tmp_bytes += size
            except OSError:
                continue

    total_mb = (total_orphan_bytes + total_tmp_bytes) / (1024 * 1024)

    if not confirm:
        sample = "\n".join(f"  • <code>{rel}</code> ({size/1024:.0f} KB)" for _f, rel, size in orphan_files[:10])
        text = (
            f"📊 <b>Skanerlash natijasi</b>\n\n"
            f"🗑 Etim fayllar (DB'da endi hech qanday yozuv ishlatmayapti): {len(orphan_files)} ta\n"
            f"🧹 Eski vaqtinchalik papkalar (24 soatdan katta): {len(stale_tmp_dirs)} ta\n"
            f"💾 Jami bo'shaydigan joy: ~{total_mb:.1f} MB\n"
        )
        if sample:
            text += f"\n🔍 Namuna (birinchi 10 tasi):\n{sample}"
            if len(orphan_files) > 10:
                text += f"\n  ... va yana {len(orphan_files) - 10} ta"
        text += (
            "\n\n⚠️ Bu FAQAT hisobot — hech narsa o'chirilmadi.\n"
            "Haqiqatan o'chirish uchun: <code>/tozalash tasdiqla</code>"
        )
        await message.answer(text)
        return

    deleted_count = 0
    deleted_bytes = 0
    for full_path, _rel, size in orphan_files:
        try:
            os.remove(full_path)
            deleted_count += 1
            deleted_bytes += size
        except OSError:
            pass

    for d in stale_tmp_dirs:
        try:
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
            else:
                os.remove(d)
        except OSError:
            pass

    # Bo'shab qolgan bo'sh papkalarni ham tozalaymiz
    if os.path.isdir(BOOK_STORAGE_DIR):
        for root, dirs, files in os.walk(BOOK_STORAGE_DIR, topdown=False):
            for d in dirs:
                dpath = os.path.join(root, d)
                try:
                    if not os.listdir(dpath):
                        os.rmdir(dpath)
                except OSError:
                    pass

    freed_mb = (deleted_bytes + total_tmp_bytes) / (1024 * 1024)
    await message.answer(
        f"✅ Tozalash yakunlandi!\n\n"
        f"🗑 O'chirilgan fayllar: {deleted_count} ta\n"
        f"🧹 O'chirilgan vaqtinchalik papkalar: {len(stale_tmp_dirs)} ta\n"
        f"💾 Bo'shagan joy: ~{freed_mb:.1f} MB"
    )



async def cmd_checkbook(message: Message):
    """Diagnostika: kitobning DB'dagi sahifalari serverning diskida
    (Railway Volume) haqiqatan mavjudligini tekshiradi. Muammoni
    "fayl saqlanmagan" (kod xatosi) yoki "fayl keyin o'chib ketgan"
    (Volume/deploy muammosi) ekanini ajratish uchun ishlatiladi."""
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("❗ Foydalanish: <code>/checkbook 5</code> (5 — kitob ID raqami)")
        return
    try:
        book_id = int(parts[1])
    except ValueError:
        await message.answer("❗ Kitob ID raqam bo'lishi kerak.")
        return

    book = await db.get_book(book_id)
    if not book:
        await message.answer(f"❌ {book_id} ID'li kitob topilmadi.")
        return

    pages = await db.get_book_pages(book_id)
    if not pages:
        await message.answer(f"⚠️ \"{book['title']}\" kitobida sahifalar umuman yo'q (page_count=0).")
        return

    missing = []
    present_count = 0
    for p in pages:
        if p["page_type"] == "image":
            full_path = os.path.join(BOOK_STORAGE_DIR, p["image_path"] or "")
            if os.path.exists(full_path):
                present_count += 1
            else:
                missing.append((p["page_number"], p["image_path"]))
        else:
            present_count += 1  # matn sahifalar disk fayliga bog'liq emas

    cover_line = ""
    if book["cover_path"]:
        cover_full = os.path.join(BOOK_STORAGE_DIR, book["cover_path"])
        cover_line = f"\n🖼 Muqova: {'✅ bor' if os.path.exists(cover_full) else '❌ topilmadi'} (<code>{cover_full}</code>)"

    text = (
        f"📕 <b>{book['title']}</b> (ID: {book_id})\n"
        f"📄 Jami sahifa (DB'da): {len(pages)}\n"
        f"✅ Diskda mavjud: {present_count}\n"
        f"❌ Diskda yo'q: {len(missing)}"
        f"{cover_line}\n\n"
        f"📁 Tekshirilgan asosiy papka: <code>{BOOK_STORAGE_DIR}</code>"
    )
    if missing:
        sample = missing[:5]
        sample_lines = "\n".join(f"  • sahifa {n}: <code>{path}</code>" for n, path in sample)
        text += f"\n\n🔍 Yo'q fayllardan namuna:\n{sample_lines}"
        if len(missing) > 5:
            text += f"\n  ... va yana {len(missing) - 5} ta"

    await message.answer(text)


@dp.message(Command("premiumbook"))
async def cmd_premiumbook(message: Message):
    """Kitobni 'faqat Premium a'zolarga ochiq' deb belgilaydi yoki bekor qiladi (bir buyruq — ikkalasi ham)."""
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("❗ Foydalanish: <code>/premiumbook 5</code> (5 — kitob ID raqami)")
        return
    try:
        book_id = int(parts[1])
    except ValueError:
        await message.answer("❗ Kitob ID raqam bo'lishi kerak.")
        return

    book = await db.get_book(book_id)
    if not book:
        await message.answer(f"❌ {book_id} ID'li kitob topilmadi.")
        return

    new_value = await db.toggle_book_premium_only(book_id)
    if new_value:
        await message.answer(f"👑 \"{book['title']}\" endi FAQAT Premium a'zolarga ochiq bo'ldi.")
    else:
        await message.answer(f"✅ \"{book['title']}\" endi hammaga (referral/koin orqali) ochiq.")

    if target_id != message.from_user.id and amount > 0:
        try:
            await bot.send_message(target_id, f"🎁 Sizga admin tomonidan {amount} koin qo'shildi!")
        except Exception:
            pass


@dp.message(Command("addpremium"))
async def cmd_addpremium(message: Message):
    if not is_super_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat bosh administratorlar uchun.")
        return

    parts = message.text.split()
    try:
        target_id = int(parts[1]) if len(parts) >= 2 else message.from_user.id
        days = int(parts[2]) if len(parts) >= 3 else PREMIUM_DURATION_DAYS
    except ValueError:
        await message.answer(
            "❗ Foydalanish:\n"
            "<code>/addpremium 123456789</code>\n"
            "<code>/addpremium 123456789 14</code>"
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
                f"⭐ Sizga admin tomonidan {days} kunlik Premium berildi!\n"
                f"Muddati: {expires.strftime('%Y-%m-%d')} gacha."
            )
        except Exception:
            pass


@dp.message(Command("tanlov"))
async def cmd_tanlov(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat administratorlar uchun.")
        return

    tmp_dir = os.path.join(BOOK_TMP_DIR, f"draw_{message.from_user.id}_{int(datetime.now().timestamp())}")
    os.makedirs(tmp_dir, exist_ok=True)

    await state.set_state(SetDrawStates.goal)
    await state.update_data(tmp_dir=tmp_dir)
    await message.answer(
        "🏆 Yangi tanlov tashkil qilamiz.\n\n"
        "👥 Necha ta referral (do'st taklif qilish) talab qilinsin?\n"
        "Shart bo'lmasa (faqat kanalga obuna yetarli bo'lsa) — 0 deb yozing:\n\n"
        "(istalgan vaqt bekor qilish uchun /bekor)"
    )


@dp.message(SetDrawStates.goal)
async def process_draw_goal(message: Message, state: FSMContext):
    try:
        goal = int((message.text or "").strip())
        if goal < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, butun son yuboring (masalan: 0 yoki 5).")
        return

    await state.update_data(goal=goal)
    await state.set_state(SetDrawStates.days)
    await message.answer("📅 Tanlov necha kun davom etadi? (masalan: 20)")


@dp.message(SetDrawStates.days)
async def process_draw_days(message: Message, state: FSMContext):
    try:
        days = int((message.text or "").strip())
        if days <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, musbat butun son yuboring (masalan: 20).")
        return

    await state.update_data(days=days)
    await state.set_state(SetDrawStates.channels)
    await message.answer(
        "📢 Qatnashish uchun majburiy obuna bo'lish kerak bo'lgan kanallarni yuboring.\n"
        "Bir nechtasini vergul bilan ajratib yozing (masalan: @kanal1, @kanal2).\n\n"
        "Shart bo'lmasa — 0 deb yozing:"
    )


@dp.message(SetDrawStates.channels)
async def process_draw_channels(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text == "0":
        channels_str = ""
    else:
        raw_channels = [c.strip() for c in text.split(",") if c.strip()]
        fixed_channels = []
        for c in raw_channels:
            if not c.startswith("@"):
                c = "@" + c
            fixed_channels.append(c)
        if not fixed_channels:
            await message.answer("❗ Iltimos, kamida bitta kanal yuboring, yoki shart bo'lmasa 0 deb yozing.")
            return
        channels_str = ",".join(fixed_channels)

    await state.update_data(required_channels=channels_str)
    await state.set_state(SetDrawStates.prizes)
    await message.answer(
        "🎁 Sovg'alar ro'yxatini yuboring — har birini alohida qatorda "
        "(maksimal 5 ta). Masalan:\n\n"
        "1-o'rin: \"Men\" kitobi\n"
        "2-o'rin: \"Sir\" kitobi\n"
        "3-o'rin: \"A'mo\" kitobi"
    )


@dp.message(SetDrawStates.prizes)
async def process_draw_prizes(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    lines = [l.strip() for l in text.split("\n") if l.strip()][:5]
    if not lines:
        await message.answer("❗ Iltimos, kamida bitta sovg'a yozing.")
        return

    await state.update_data(prizes="\n".join(lines))
    await state.set_state(SetDrawStates.summary)
    await message.answer(
        "📝 Tanlov uchun qisqa sarlavha/sovg'a matnini yozing "
        "(masalan: \"5 ta kitob sovg'asi\"):"
    )


@dp.message(SetDrawStates.summary)
async def process_draw_summary(message: Message, state: FSMContext):
    summary = (message.text or "").strip()
    if not summary:
        await message.answer("❗ Iltimos, matn yuboring.")
        return

    await state.update_data(reward_text=summary)
    await state.set_state(SetDrawStates.image)
    await message.answer(
        "🖼 Tanlov uchun rasm (banner) yuboring.\n"
        "Rasmsiz davom etish uchun /otkazib_yuborish deb yozing:"
    )


async def _finalize_draw(message: Message, state: FSMContext, image_tmp_path: str = None):
    data = await state.get_data()
    draw = await db.set_weekly_draw(
        goal=data["goal"],
        reward_text=data["reward_text"],
        days=data["days"],
        required_channels=data.get("required_channels", ""),
        prizes=data.get("prizes", ""),
    )

    if image_tmp_path:
        final_dir = os.path.join(BOOK_STORAGE_DIR, "draws", str(draw["id"]))
        os.makedirs(final_dir, exist_ok=True)
        final_rel = f"draws/{draw['id']}/image.jpg"
        final_abs = os.path.join(BOOK_STORAGE_DIR, final_rel)
        shutil.move(image_tmp_path, final_abs)
        await db.set_draw_image_path(draw["id"], final_rel)

    tmp_dir = data.get("tmp_dir")
    if tmp_dir and os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)
    await state.clear()

    channels_display = data.get("required_channels") or "Shart emas"
    prizes_display = data.get("prizes", "").replace("\n", "\n🎁 ")

    await message.answer(
        f"✅ Tanlov yaratildi!\n\n"
        f"🎯 Referral shart: {data['goal'] if data['goal'] > 0 else 'Shart emas'}\n"
        f"📅 Muddat: {draw['week_start']} — {draw['week_end']} ({data['days']} kun)\n"
        f"📢 Majburiy kanallar: {channels_display}\n"
        f"🎁 {prizes_display}\n\n"
        f"Mini App'dagi Bosh sahifada avtomatik ko'rinadi. "
        f"G'oliblarni aniqlash uchun /admin dan \"Tasodifiy g'olibni aniqlash\"ni ishlating."
    )


@dp.message(SetDrawStates.image, F.photo)
async def process_draw_image(message: Message, state: FSMContext):
    data = await state.get_data()
    tmp_dir = data["tmp_dir"]
    image_path = os.path.join(tmp_dir, "draw_image.jpg")
    await bot.download(message.photo[-1], destination=image_path)
    await _finalize_draw(message, state, image_tmp_path=image_path)


@dp.message(SetDrawStates.image, Command("otkazib_yuborish"))
async def skip_draw_image(message: Message, state: FSMContext):
    await _finalize_draw(message, state, image_tmp_path=None)


@dp.message(SetDrawStates.image)
async def process_draw_image_invalid(message: Message):
    await message.answer("❗ Iltimos, rasm yuboring, yoki /otkazib_yuborish deb yozing.")


@dp.message(Command("tanlov_holati"))
async def cmd_tanlov_holati(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat administratorlar uchun.")
        return

    draw = await db.get_current_weekly_draw()
    if not draw:
        await message.answer("Hozircha faol tanlov yo'q. /tanlov bilan yarating.")
        return

    participants = await db.get_weekly_participant_count(draw["week_start"], draw["goal"])
    days_left = "tugagan"
    try:
        end_dt = datetime.strptime(draw["week_end"], "%Y-%m-%d")
        remaining = (end_dt - datetime.now()).days
        days_left = f"{max(0, remaining)} kun qoldi"
    except Exception:
        pass

    channels_display = draw["required_channels"] or "Shart emas"
    prizes_display = (draw["prizes"] or draw["reward_text"]).replace("\n", "\n🎁 ")

    await message.answer(
        f"🏆 <b>Joriy tanlov</b>\n\n"
        f"🎯 Referral shart: {draw['goal'] if draw['goal'] > 0 else 'Shart emas'}\n"
        f"📢 Majburiy kanallar: {channels_display}\n"
        f"🎁 {prizes_display}\n"
        f"📅 {draw['week_start']} — {draw['week_end']} ({days_left})\n"
        f"👥 Shartni bajarganlar: {participants} kishi"
    )


@dp.message(Command("tanlov_ochir"))
async def cmd_tanlov_ochir(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat administratorlar uchun.")
        return

    closed = await db.deactivate_weekly_draw()
    if closed:
        await message.answer("✅ Joriy tanlov o'chirildi — endi ilovada ko'rinmaydi.")
    else:
        await message.answer("Hozircha faol tanlov topilmadi.")


@dp.message(Command("kanal_qosh"))
async def cmd_kanal_qosh(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat administratorlar uchun.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❗ Foydalanish: <code>/kanal_qosh @kanal_nomi</code>")
        return

    channel = parts[1].strip()
    if not channel.startswith("@"):
        channel = "@" + channel

    await db.add_force_channel(channel)
    channels = await db.get_active_force_channels()
    await message.answer(
        f"✅ {channel} majburiy obuna ro'yxatiga qo'shildi.\n\n"
        f"📢 Hozirgi ro'yxat ({len(channels)} ta):\n" + "\n".join(channels) +
        f"\n\n⚠️ Diqqat: botingiz shu kanalda <b>admin</b> bo'lishi shart, "
        f"aks holda obunani tekshira olmaydi."
    )


@dp.message(Command("kanal_ochir"))
async def cmd_kanal_ochir(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat administratorlar uchun.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❗ Foydalanish: <code>/kanal_ochir @kanal_nomi</code>")
        return

    channel = parts[1].strip()
    if not channel.startswith("@"):
        channel = "@" + channel

    removed = await db.remove_force_channel(channel)
    if removed:
        channels = await db.get_active_force_channels()
        remaining = "\n".join(channels) if channels else "(ro'yxat bo'sh)"
        await message.answer(f"✅ {channel} ro'yxatdan olib tashlandi.\n\n📢 Qolgan kanallar:\n{remaining}")
    else:
        await message.answer(f"❗ {channel} faol ro'yxatda topilmadi.")


@dp.message(Command("kanallar"))
async def cmd_kanallar(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat administratorlar uchun.")
        return

    channels = await db.get_active_force_channels()
    if not channels:
        await message.answer("Hozircha majburiy obuna kanallari yo'q.")
        return
    await message.answer("📢 <b>Majburiy obuna kanallari:</b>\n\n" + "\n".join(channels))


@dp.message(Command("addrefs"))
async def cmd_addrefs(message: Message):
    if not is_super_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat bosh administratorlar uchun.")
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
            "❗ Foydalanish (sinov/test maqsadida sun'iy referral qo'shadi):\n"
            "<code>/addrefs 5</code> — o'zingizga 5 ta referral qo'shish\n"
            "<code>/addrefs 123456789 5</code> — boshqa foydalanuvchiga qo'shish\n"
            "(manfiy son yozsangiz — ayiradi)"
        )
        return

    new_total = await db.add_bonus_referrals(target_id, amount)
    await message.answer(
        f"✅ <code>{target_id}</code> uchun {amount:+d} referral (test rejimida). "
        f"Yangi umumiy referral soni: {new_total}"
    )


@dp.callback_query(F.data == "admin_randomizer")
async def callback_admin_randomizer(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q", show_alert=True)
        return

    draw = await db.get_current_weekly_draw()
    if draw:
        week_label = f"{draw['week_start']} — {draw['week_end']}"
        prizes_list = [p for p in (draw["prizes"] or draw["reward_text"]).split("\n") if p.strip()]
        required_channels = [c for c in (draw["required_channels"] or "").split(",") if c.strip()]
    else:
        week_label = datetime.now().strftime("%Y-%m-%d")
        prizes_list = ["Sovg'a"]
        required_channels = []

    winners = await db.get_random_eligible_winners(
        min_referrals=(draw["goal"] if draw else 0),
        count=len(prizes_list),
    )
    if not winners:
        await callback.answer("Mos ishtirokchi topilmadi", show_alert=True)
        return

    await callback.answer("⏳ G'oliblar tekshirilmoqda...")

    result_lines = [f"🎉 <b>G'oliblar aniqlandi!</b> ({week_label})\n"]
    for i, winner in enumerate(winners):
        prize = prizes_list[i] if i < len(prizes_list) else "Sovg'a"

        if required_channels:
            not_subbed = await check_subscription(winner["user_id"], required_channels)
            sub_status = "✅ barcha kanallarga a'zo" if not not_subbed else f"⚠️ a'zo emas: {', '.join(not_subbed)}"
        else:
            sub_status = ""

        await db.add_winner(winner["user_id"], winner["username"], week_label, winner["cnt"], prize)

        name = winner["username"] or str(winner["user_id"])
        line = f"{i + 1}. 👤 {name} — 🎁 {prize}"
        if sub_status:
            line += f"\n   {sub_status}"
        result_lines.append(line)

        try:
            await bot.send_message(
                winner["user_id"],
                f"🎉 Tabriklaymiz! Siz tanlovda g'olib bo'ldingiz!\n🎁 Sovg'a: {prize}"
            )
        except Exception:
            pass

    result_lines.append("\nNatijalar 'So'nggi g'oliblar' ro'yxatiga saqlandi.")
    if any("a'zo emas" in line for line in result_lines):
        result_lines.append(
            "\n⚠️ Diqqat: yuqorida \"a'zo emas\" deb belgilangan g'oliblar barcha "
            "shart qilingan kanallarga a'zo emas — xohlasangiz ularni almashtirib, "
            "qayta tasodifiy tanlovni ishga tushirishingiz mumkin."
        )

    await callback.message.answer("\n\n".join(result_lines))



@dp.callback_query(F.data == "admin_stats")
async def callback_admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q", show_alert=True)
        return

    users = await db.get_all_users()
    total_users = len(users)
    total_coins = sum((u["coins"] or 0) for u in users)
    premium_count = sum(1 for u in users if u["is_premium"])

    await callback.message.answer(
        f"📊 <b>Statistika</b>\n\n"
        f"👥 Jami foydalanuvchilar: {total_users}\n"
        f"🪙 Foydalanuvchilarda jami koin: {total_coins}\n"
        f"⭐ Premium foydalanuvchilar: {premium_count}"
    )
    await callback.answer()


# ---------- ADMIN: KITOB QO'SHISH OQIMI ----------

@dp.message(Command("bekor"))
async def cmd_cancel_flow(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        return
    data = await state.get_data()
    tmp_dir = data.get("tmp_dir")
    if tmp_dir and os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)
    await state.clear()
    await message.answer("❌ Kitob qo'shish jarayoni bekor qilindi.")


# ================== STARS / PREMIUM SOTIB OLISH ==================

@dp.message(Command("buy_premium"))
async def cmd_buy_premium(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 oy", callback_data="buy_prem_1")],
        [InlineKeyboardButton(text=f"⭐ 3 oy — {PREMIUM_STARS_PRICE[3]} Stars", callback_data="buy_prem_3")],
        [InlineKeyboardButton(text=f"⭐ 6 oy — {PREMIUM_STARS_PRICE[6]} Stars", callback_data="buy_prem_6")],
        [InlineKeyboardButton(text=f"⭐ 12 oy — {PREMIUM_STARS_PRICE[12]} Stars", callback_data="buy_prem_12")],
    ])
    await message.answer(
        "👑 <b>Telegram Premium sotib olish</b>\n\n"
        "3 / 6 / 12 oylik — to'lovdan so'ng avtomatik ulanadi.\n"
        "1 oylik — koin yoki Stars bilan to'lash mumkin, admin qo'lda olib beradi.",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data == "buy_prem_1")
async def process_buy_premium_1(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🪙 Koin bilan ({PREMIUM_1MONTH_COIN_PRICE:,})", callback_data="prem1_coin")],
        [InlineKeyboardButton(text=f"⭐ Stars bilan ({PREMIUM_1MONTH_STARS_PRICE})", callback_data="prem1_stars")],
    ])
    await callback.message.answer(
        "1 oylik Premium uchun to'lov usulini tanlang:",
        reply_markup=keyboard,
    )
    await callback.answer()


@dp.callback_query(F.data == "prem1_coin")
async def process_buy_premium_1_coin(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await db.get_user(user_id)
    if not user or user["coins"] < PREMIUM_1MONTH_COIN_PRICE:
        await callback.answer("❌ Koiningiz yetarli emas.", show_alert=True)
        return

    await db.add_coins(user_id, -PREMIUM_1MONTH_COIN_PRICE)
    order_id = await db.create_premium_order(user_id, months=1, stars_paid=0)
    await callback.message.answer(
        "✅ To'lov qabul qilindi (koin orqali)!\n"
        "1 oylik Premium buyurtmangiz qabul qilindi. Admin tez orada olib beradi."
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🪙 Yangi 1 oylik Premium buyurtma (KOIN orqali)!\n"
                f"User: <code>{user_id}</code>\n"
                f"To'landi: {PREMIUM_1MONTH_COIN_PRICE:,} koin\n"
                f"Order ID: {order_id}\n"
                f"/complete_premium_{order_id}"
            )
        except Exception:
            pass
    await callback.answer()


@dp.callback_query(F.data == "prem1_stars")
async def process_buy_premium_1_stars(callback: CallbackQuery):
    user_id = callback.from_user.id
    prices = [LabeledPrice(label="1 oylik Telegram Premium", amount=PREMIUM_1MONTH_STARS_PRICE)]
    await bot.send_invoice(
        chat_id=user_id,
        title="1 oylik Telegram Premium",
        description="To'lovdan so'ng admin sizga Premiumni qo'lda olib beradi.",
        payload=f"premium_1_{user_id}",
        provider_token="",
        currency="XTR",
        prices=prices,
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("buy_prem_"))
async def process_buy_premium_months(callback: CallbackQuery):
    months = int(callback.data.split("_")[-1])
    if months not in PREMIUM_STARS_PRICE:
        return
    user_id = callback.from_user.id
    stars = PREMIUM_STARS_PRICE[months]
    prices = [LabeledPrice(label=f"{months} oylik Telegram Premium", amount=stars)]
    await bot.send_invoice(
        chat_id=user_id,
        title=f"{months} oylik Telegram Premium",
        description=f"To'lovdan so'ng Premium avtomatik ulanadi ({stars} Stars).",
        payload=f"premium_{months}_{user_id}",
        provider_token="",
        currency="XTR",
        prices=prices,
    )
    await callback.answer()


@dp.message(Command("buy_coins"))
async def cmd_buy_coins(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 100 Stars — 5 000 koin", callback_data="buy_coins_100_5000")],
        [InlineKeyboardButton(text="⭐ 300 Stars — 16 000 koin", callback_data="buy_coins_300_16000")],
        [InlineKeyboardButton(text="⭐ 500 Stars — 28 000 koin", callback_data="buy_coins_500_28000")],
        [InlineKeyboardButton(text="⭐ 1000 Stars — 60 000 koin", callback_data="buy_coins_1000_60000")],
    ])
    await message.answer("🪙 <b>Stars orqali koin sotib olish</b>\n\nPaketni tanlang:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("buy_coins_"))
async def process_buy_coins(callback: CallbackQuery):
    _, _, stars_str, coins_str = callback.data.split("_")
    stars, coins = int(stars_str), int(coins_str)
    user_id = callback.from_user.id
    prices = [LabeledPrice(label=f"{coins:,} koin", amount=stars)]
    await bot.send_invoice(
        chat_id=user_id,
        title=f"{coins:,} koin",
        description=f"{stars} Stars evaziga {coins:,} koin hisobingizga qo'shiladi.",
        payload=f"coins_{coins}_{user_id}",
        provider_token="",
        currency="XTR",
        prices=prices,
    )
    await callback.answer()


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payment: SuccessfulPayment = message.successful_payment
    payload = payment.invoice_payload
    user_id = message.from_user.id
    stars = payment.total_amount

    await db.record_star_payment(
        user_id=user_id,
        amount_stars=stars,
        purpose=payload,
        charge_id=payment.telegram_payment_charge_id,
    )

    # --- Koin sotib olish ---
    if payload.startswith("coins_"):
        parts = payload.split("_")
        coins = int(parts[1])
        await db.add_coins(user_id, coins)
        await message.answer(f"✅ To'lov qabul qilindi!\n🪙 +{coins:,} koin hisobingizga qo'shildi.")
        return

    # --- 1 oylik Premium (Stars orqali, admin qo'lda beradi) ---
    if payload.startswith("premium_1_"):
        order_id = await db.create_premium_order(user_id, months=1, stars_paid=stars)
        await message.answer(
            "✅ To'lov qabul qilindi!\n"
            "1 oylik Premium buyurtmangiz qabul qilindi. Admin tez orada olib beradi."
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⭐ Yangi 1 oylik Premium buyurtma (STARS orqali)!\n"
                    f"User: <code>{user_id}</code>\n"
                    f"Stars: {stars}\n"
                    f"Order ID: {order_id}\n"
                    f"/complete_premium_{order_id}"
                )
            except Exception:
                pass
        return

    # --- 3 / 6 / 12 oylik — avtomatik giftPremiumSubscription ---
    try:
        parts = payload.split("_")
        months = int(parts[1])
        if months in PREMIUM_STARS_OFFICIAL:
            official_stars = PREMIUM_STARS_OFFICIAL[months]  # aynan shuncha Stars sovg'a qilish uchun ishlatiladi
            result = await bot.gift_premium_subscription(
                user_id=user_id,
                month_count=months,
                star_count=official_stars,
                text="Kitobzor orqali Premium sovg'a qilindi! 📚✨",
            )
            if result:
                await message.answer(
                    f"🎉 Tabriklaymiz!\n{months} oylik Telegram Premium muvaffaqiyatli ulandi!"
                )
            else:
                await message.answer("To'lov qabul qilindi, lekin Premium ulashda xatolik. Admin bilan bog'laning.")
    except Exception as e:
        logger.exception("giftPremiumSubscription xatosi")
        await message.answer(
            "To'lov qabul qilindi. Premium ulashda texnik xatolik yuz berdi. Admin tez orada hal qiladi."
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"⚠️ Premium gift xatosi: {e}\nUser: {user_id}\nPayload: {payload}")
            except Exception:
                pass


@dp.message(F.text.regexp(r"^/complete_premium_(\d+)$"))
async def complete_premium_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    match = re.match(r"^/complete_premium_(\d+)$", message.text)
    order_id = int(match.group(1))
    await db.complete_premium_order(order_id, admin_note=f"Completed by {message.from_user.id}")
    await message.answer(f"✅ Buyurtma #{order_id} yakunlandi deb belgilandi.")


@dp.callback_query(F.data == "admin_premium_orders")
async def admin_premium_orders(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    orders = await db.get_pending_premium_orders(limit=20)
    if not orders:
        await callback.message.answer("✅ Kutilayotgan Premium buyurtma yo'q.")
        await callback.answer()
        return
    lines = ["⭐ <b>Kutilayotgan Premium buyurtmalar:</b>\n"]
    for o in orders:
        pay_method = "koin" if o["stars_paid"] == 0 else f"{o['stars_paid']} Stars"
        lines.append(
            f"#{o['id']} — user <code>{o['user_id']}</code> — {o['months']} oy — {pay_method}\n"
            f"/complete_premium_{o['id']}\n"
        )
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@dp.message(Command("som_buyurtmalar"))
async def cmd_som_buyurtmalar(message: Message):
    if not is_admin(message.from_user.id):
        return
    orders = await db.get_pending_uzs_orders(limit=30)
    if not orders:
        await message.answer("✅ Kutilayotgan so'mdagi buyurtma yo'q.")
        return
    lines = ["💵 <b>Kutilayotgan so'mdagi buyurtmalar:</b>\n"]
    for o in orders:
        lines.append(
            f"#{o['id']} — {o['product_name']}\n"
            f"💰 {o['price_uzs']:,} so'm\n"
            f"👤 @{o['contact_username'] or '—'} (ID: {o['user_id']})\n"
            f"📞 {o['contact_phone'] or '—'}\n"
            f"Yakunlash: /buyurtma_yakunla_{o['id']}\n"
        )
    await message.answer("\n".join(lines))


@dp.message(F.text.regexp(r"^/buyurtma_yakunla_(\d+)$"))
async def cmd_buyurtma_yakunla(message: Message):
    if not is_admin(message.from_user.id):
        return
    match = re.match(r"^/buyurtma_yakunla_(\d+)$", message.text)
    order_id = int(match.group(1))
    await db.complete_uzs_order(order_id, admin_note=f"Yakunlandi: {message.from_user.id}")
    await message.answer(f"✅ Buyurtma #{order_id} yakunlandi deb belgilandi.")


@dp.message(Command("tahrirla_dokon"))
async def cmd_tahrirla_dokon(message: Message):
    if not is_admin(message.from_user.id):
        return
    products = await db.get_active_shop_products()
    if not products:
        await message.answer("Do'konda mahsulot yo'q.")
        return
    lines = ["🛍 <b>Do'kon mahsulotlari:</b>\n"]
    for p in products:
        cost_usd_val = p["cost_usd"] if "cost_usd" in p.keys() else None
        cost_usd_display = f"${cost_usd_val:g}" if cost_usd_val else "—"
        lines.append(f"#{p['id']} — {p['name']} — {p['cost']:,} koin (tannarx: {cost_usd_display})")
    lines.append(
        "\nNarxni o'zgartirish uchun:\n<code>/narx_ozgartir ID yangi_tannarx($)</code>\n"
        "Masalan: <code>/narx_ozgartir 5 4.5</code>\n\n"
        "O'chirish uchun:\n<code>/mahsulot_ochir ID</code>"
    )
    await message.answer("\n".join(lines))


@dp.message(F.text.regexp(r"^/narx_ozgartir\s+(\d+)\s+([\d.]+)$"))
async def cmd_narx_ozgartir(message: Message):
    if not is_admin(message.from_user.id):
        return
    match = re.match(r"^/narx_ozgartir\s+(\d+)\s+([\d.]+)$", message.text)
    product_id, cost_usd = int(match.group(1)), float(match.group(2))
    new_coin_price = calculate_coin_price(cost_usd)
    await db.update_shop_product_price(product_id, cost_usd, new_coin_price)
    await message.answer(f"✅ #{product_id} narxi yangilandi: ${cost_usd:g} → {new_coin_price:,} koin")


@dp.message(F.text.regexp(r"^/mahsulot_ochir\s+(\d+)$"))
async def cmd_mahsulot_ochir(message: Message):
    if not is_admin(message.from_user.id):
        return
    match = re.match(r"^/mahsulot_ochir\s+(\d+)$", message.text)
    product_id = int(match.group(1))
    await db.delete_shop_product(product_id)
    await message.answer(f"✅ #{product_id} do'kondan o'chirildi.")


@dp.message(F.text.regexp(r"^/kitob_ochir\s+(\d+)$"))
async def cmd_kitob_ochir(message: Message):
    if not is_admin(message.from_user.id):
        return
    match = re.match(r"^/kitob_ochir\s+(\d+)$", message.text)
    book_id = int(match.group(1))
    await db.delete_book(book_id)
    await message.answer(f"✅ Kitob #{book_id} o'chirildi.")


@dp.message(Command("kitoblar_id"))
async def cmd_kitoblar_id(message: Message):
    if not is_admin(message.from_user.id):
        return
    books = await db.get_all_books()
    if not books:
        await message.answer("📚 Hozircha hech qanday kitob qo'shilmagan.")
        return

    lines = ["📚 <b>Kitoblar ro'yxati (ID bo'yicha)</b>\n"]
    for b in books:
        lines.append(f"🆔 <code>{b['id']}</code> — {b['title']} ({b['author']})")

    text = "\n".join(lines)
    # Telegram xabar uzunligi cheklovi (~4096 belgi) tufayli, uzun ro'yxatlarni bo'lib yuboramiz
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3800:
            await message.answer(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        await message.answer(chunk)


@dp.message(F.text.regexp(r"^/vazifa_ochir\s+(\d+)$"))
async def cmd_vazifa_ochir(message: Message):
    if not is_admin(message.from_user.id):
        return
    match = re.match(r"^/vazifa_ochir\s+(\d+)$", message.text)
    task_id = int(match.group(1))
    await db.deactivate_task(task_id)
    await message.answer(f"✅ Vazifa #{task_id} o'chirildi.")


@dp.message(F.text.regexp(r"^/test_ochir\s+(\d+)$"))
async def cmd_test_ochir(message: Message):
    if not is_admin(message.from_user.id):
        return
    match = re.match(r"^/test_ochir\s+(\d+)$", message.text)
    quiz_id = int(match.group(1))
    await db.deactivate_quiz_set(quiz_id)
    await message.answer(f"✅ Test #{quiz_id} o'chirildi.")


@dp.message(Command("sandiqlar"))
async def cmd_sandiqlar(message: Message):
    if not is_admin(message.from_user.id):
        return
    chests = await db.get_active_chests()
    if not chests:
        await message.answer("Hozircha sandiq yo'q.")
        return
    lines = ["📦 <b>Faol sandiqlar:</b>\n"]
    for c in chests:
        reward_lines = []
        for r in c["rewards"]:
            if r["type"] == "coins":
                reward_lines.append(f"  • {r['chance']}% — {r['min']}-{r['max']} koin")
            else:
                reward_lines.append(f"  • {r['chance']}% — {r['days']} kun Premium")
        lines.append(
            f"{c['emoji']} <b>{c['name']}</b> (id: <code>{c['id']}</code>)\n"
            f"Narxi: {c['cost']} koin | Kunlik limit: {c['daily_limit']}\n" +
            "\n".join(reward_lines) + "\n"
        )
    await message.answer("\n".join(lines))


@dp.message(Command("sandiq_qosh"))
async def cmd_sandiq_qosh(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "📦 Yangi sandiq qo'shish uchun quyidagi formatda yuboring:\n\n"
        "<code>/sandiq_yarat id|nom|emoji|narx|kunlik_limit|mukofot1,mukofot2</code>\n\n"
        "Mukofot turlari:\n"
        "<code>coins:min-max:foiz</code> — koin\n"
        "<code>premium:kun:foiz</code> — bot Premium\n"
        "<code>badge:foiz</code> — faxriy nishon\n"
        "<code>streak_freeze:foiz</code> — +1 streak-freeze\n"
        "<code>nothing:foiz</code> — hech narsa chiqmaydi\n\n"
        "Misol:\n"
        "<code>/sandiq_yarat kumush|Kumush sandiq|🥈|70|4|coins:40-90:70,badge:15,streak_freeze:10,nothing:5</code>"
    )


@dp.message(F.text.regexp(r"^/sandiq_yarat\s+(.+)$"))
async def cmd_sandiq_yarat(message: Message):
    if not is_admin(message.from_user.id):
        return
    try:
        raw = message.text.split(maxsplit=1)[1]
        chest_id, name, emoji, cost, daily_limit, rewards_raw = [p.strip() for p in raw.split("|")]
        rewards = []
        skipped = []
        for part in rewards_raw.split(","):
            # Har bir bo'lakni (va uning ":" bilan ajratilgan ichki qismlarini) alohida
            # tozalaymiz — shunda "coins : 50-333 : 50" kabi bo'shliqli format ham
            # "coins:50-333:50" kabi to'g'ri tanib olinadi.
            fields = [f.strip() for f in part.strip().split(":")]
            kind = fields[0].lower()
            if kind == "coins":
                min_v, max_v = [v.strip() for v in fields[1].split("-")]
                rewards.append({"type": "coins", "min": int(min_v), "max": int(max_v), "chance": int(fields[2])})
            elif kind == "premium":
                rewards.append({"type": "premium", "days": int(fields[1]), "chance": int(fields[2])})
            elif kind == "badge":
                rewards.append({"type": "badge", "chance": int(fields[1])})
            elif kind == "streak_freeze":
                rewards.append({"type": "streak_freeze", "chance": int(fields[1])})
            elif kind == "nothing":
                rewards.append({"type": "nothing", "chance": int(fields[1])})
            else:
                skipped.append(part.strip())
        if skipped:
            await message.answer(
                "❗ Quyidagi mukofot(lar) tanilmadi (\"coins\", \"premium\", \"badge\", "
                "\"streak_freeze\" yoki \"nothing\" bilan boshlanishi kerak): "
                + "; ".join(skipped)
            )
            return
        total_chance = sum(r["chance"] for r in rewards)
        if total_chance != 100:
            await message.answer(f"❗ Ehtimolliklar yig'indisi 100% bo'lishi kerak (hozir: {total_chance}%).")
            return
        await db.add_chest(chest_id.strip(), name.strip(), emoji.strip(), int(cost), int(daily_limit), rewards)
        await message.answer(f"✅ '{name.strip()}' sandig'i qo'shildi/yangilandi!")
    except Exception as e:
        await message.answer(f"❗ Format xato: {e}\n\n/sandiq_qosh orqali to'g'ri formatni ko'ring.")


@dp.message(F.text.regexp(r"^/sandiq_ochir\s+(\S+)$"))
async def cmd_sandiq_ochir(message: Message):
    if not is_admin(message.from_user.id):
        return
    chest_id = message.text.split(maxsplit=1)[1].strip()
    await db.deactivate_chest(chest_id)
    await message.answer(f"✅ '{chest_id}' sandig'i o'chirildi.")


@dp.message(Command("moliya"))
async def cmd_moliya(message: Message):
    if not is_super_admin(message.from_user.id):
        return
    stats = await db.get_financial_stats()
    await message.answer(
        "💰 <b>Moliyaviy hisobot</b>\n\n"
        f"⭐ Jami Stars tushumi: {stats['total_stars']:,}\n"
        f"📦 Jami savdolar soni: {stats['total_payments']}\n"
        f"👑 Premium buyurtmalar (kutilmoqda): {stats['pending_premium']}\n"
        f"✅ Premium buyurtmalar (yakunlangan): {stats['completed_premium']}\n"
    )


@dp.message(Command("statistika"))
async def cmd_statistika(message: Message):
    if not is_admin(message.from_user.id):
        return
    stats = await db.get_usage_stats()
    await message.answer(
        "📊 <b>Statistika</b>\n\n"
        f"👥 Jami foydalanuvchilar: {stats['total_users']}\n"
        f"🆕 Bugun qo'shilgan: {stats['new_today']}\n"
        f"🆕 Shu hafta qo'shilgan: {stats['new_week']}\n"
        f"🆕 Shu oy qo'shilgan: {stats['new_month']}\n"
        f"📖 Bugun faol o'quvchilar: {stats['active_today']}\n"
    )


@dp.message(Command("addbook"))
async def cmd_addbook(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    tmp_dir = os.path.join(BOOK_TMP_DIR, f"admin_{message.from_user.id}_{int(datetime.now().timestamp())}")
    os.makedirs(tmp_dir, exist_ok=True)

    await state.set_state(AddBookStates.title)
    await state.update_data(tmp_dir=tmp_dir)
    await message.answer(
        "📕 Yangi kitob qo'shamiz.\n\nKitob nomini yuboring "
        "(istalgan vaqt bekor qilish uchun /bekor):"
    )


@dp.message(AddBookStates.title)
async def process_book_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title:
        await message.answer("❗ Iltimos, matn ko'rinishida kitob nomini yuboring.")
        return
    await state.update_data(title=title)
    await state.set_state(AddBookStates.author)
    await message.answer("✍️ Endi muallifining ismini yuboring:")


@dp.message(AddBookStates.author)
async def process_book_author(message: Message, state: FSMContext):
    author = (message.text or "").strip()
    if not author:
        await message.answer("❗ Iltimos, matn ko'rinishida muallif ismini yuboring.")
        return
    await state.update_data(author=author)
    await state.set_state(AddBookStates.cover)
    await message.answer("🖼 Endi kitobning muqova rasmini yuboring (rasm sifatida):")


@dp.message(AddBookStates.cover, F.photo)
async def process_book_cover(message: Message, state: FSMContext):
    data = await state.get_data()
    tmp_dir = data["tmp_dir"]
    cover_path = os.path.join(tmp_dir, "cover.jpg")

    await bot.download(message.photo[-1], destination=cover_path)

    await state.update_data(cover_tmp_path=cover_path)
    await state.set_state(AddBookStates.genre)
    await message.answer("🏷 Endi janrini yuboring (masalan: Roman, Diniy, Bolalar adabiyoti):")


@dp.message(AddBookStates.cover)
async def process_book_cover_invalid(message: Message):
    await message.answer("❗ Iltimos, muqova uchun rasm (📷) yuboring, matn emas.")


@dp.message(AddBookStates.genre)
async def process_book_genre(message: Message, state: FSMContext):
    genre = (message.text or "").strip()
    if not genre:
        await message.answer("❗ Iltimos, matn ko'rinishida janrini yuboring.")
        return
    await state.update_data(genre=genre)
    await state.set_state(AddBookStates.pdf)
    await message.answer("📄 Endi kitobning PDF faylini yuboring:")


MAX_TELEGRAM_DOWNLOAD_SIZE = 20 * 1024 * 1024  # Telegram Bot API cheklovi: 20 MB
PDF_CONVERT_TIMEOUT_SECONDS = 600  # 10 daqiqa — bundan uzoq davom etsa, to'xtatiladi


@dp.message(AddBookStates.pdf, F.document)
async def process_book_pdf(message: Message, state: FSMContext):
    doc = message.document
    filename = (doc.file_name or "").lower()
    is_pdf = (doc.mime_type == "application/pdf") or filename.endswith(".pdf")
    if not is_pdf:
        await message.answer("❗ Iltimos, PDF formatidagi faylni yuboring.")
        return

    if doc.file_size and doc.file_size > MAX_TELEGRAM_DOWNLOAD_SIZE:
        size_mb = doc.file_size / (1024 * 1024)
        await message.answer(
            f"❌ Fayl juda katta ({size_mb:.1f} MB).\n\n"
            f"Telegram bot API orqali faqat <b>20 MB</b>gacha bo'lgan fayllarni yuklab olish mumkin "
            f"— bu Telegramning o'zining cheklovi.\n\n"
            f"Iltimos, PDF'ni biror siqish (compress) ilovasi orqali (masalan \"PDF siqish\", "
            f"Smallpdf, iLovePDF) 20 MB dan kichik qilib, qaytadan yuboring."
        )
        return

    data = await state.get_data()
    tmp_dir = data["tmp_dir"]
    pdf_path = os.path.join(tmp_dir, "book.pdf")

    await message.answer("⏳ PDF yuklanmoqda va sahifalarga aylantirilmoqda, biroz kuting...")

    try:
        await bot.download(doc, destination=pdf_path)
    except Exception as e:
        logger.exception("PDF yuklab olishda xato")
        await message.answer(
            f"❌ Faylni yuklab olishda xatolik yuz berdi: {e}\n\n"
            f"Fayl hajmi katta bo'lsa, uni siqib qaytadan yuboring, "
            f"yoki boshqa PDF bilan urinib ko'ring (/bekor — bekor qilish)."
        )
        return

    pages_dir = os.path.join(tmp_dir, "pages")
    try:
        page_items = await asyncio.wait_for(
            asyncio.to_thread(convert_pdf_hybrid, pdf_path, pages_dir),
            timeout=PDF_CONVERT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        await message.answer(
            "❌ PDF'ni qayta ishlash juda uzoq davom etdi (10 daqiqadan oshdi) va to'xtatildi.\n"
            "Ehtimol, kitob juda katta yoki murakkab. Kichikroq/soddaroq PDF bilan urinib ko'ring, "
            "yoki /bekor bilan to'xtating."
        )
        return
    except Exception as e:
        logger.exception("PDF konvertatsiya xatosi")
        await message.answer(
            f"❌ PDF'ni qayta ishlashda xatolik yuz berdi: {e}\n"
            f"Boshqa PDF fayl bilan urinib ko'ring, yoki /bekor bilan to'xtating."
        )
        return

    if not page_items:
        await message.answer("❗ PDF'dan hech qanday sahifa chiqmadi. Boshqa fayl yuboring, yoki /bekor.")
        return

    text_count = sum(1 for p in page_items if p["type"] == "text")
    image_count = len(page_items) - text_count

    await state.update_data(pdf_page_items=page_items, pdf_page_count=len(page_items))
    await state.set_state(AddBookStates.referrals)
    await message.answer(
        f"✅ {len(page_items)} ta sahifaga aylantirildi "
        f"(📝 {text_count} ta matn, 🖼 {image_count} ta rasm sifatida).\n\n"
        f"👥 Bu kitobni ochish uchun nechta referral (taklif qilingan do'st) kerak? "
        f"(shart bo'lmasa 0 yozing):"
    )


@dp.message(AddBookStates.pdf)
async def process_book_pdf_invalid(message: Message):
    await message.answer("❗ Iltimos, PDF faylni hujjat (📎) sifatida yuboring.")


@dp.message(AddBookStates.referrals)
async def process_book_referrals(message: Message, state: FSMContext):
    try:
        referrals = int((message.text or "").strip())
        if referrals < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, butun son yuboring (masalan: 0 yoki 5).")
        return

    await state.update_data(required_referrals=referrals)
    await state.set_state(AddBookStates.coins)
    await message.answer(
        "💵 Bu kitobning tannarxini dollarda yozing (masalan: 2 yoki 3.5).\n"
        "Agar kitob bepul (faqat referral orqali) bo'lsa — 0 yozing:"
    )


@dp.message(AddBookStates.coins)
async def process_book_coins(message: Message, state: FSMContext):
    try:
        cost_usd = float((message.text or "").strip().replace(",", "."))
        if cost_usd < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, musbat son yuboring (masalan: 0 yoki 2.5).")
        return

    coins_required = 0 if cost_usd == 0 else calculate_coin_price(cost_usd)

    data = await state.get_data()
    tmp_dir = data["tmp_dir"]
    title = data["title"]
    author = data["author"]
    genre = data["genre"]
    cover_tmp_path = data["cover_tmp_path"]
    required_referrals = data["required_referrals"]
    page_items = data["pdf_page_items"]

    await message.answer(
        f"✅ Hisoblandi: ${cost_usd:g} → <b>{coins_required:,}</b> koin\n\n💾 Saqlanmoqda..."
        if cost_usd > 0 else "💾 Saqlanmoqda..."
    )

    try:
        book_id = await db.add_book(title, author, genre, "", required_referrals, coins_required, cost_usd=cost_usd)

        final_dir = os.path.join(BOOK_STORAGE_DIR, str(book_id))
        final_pages_dir = os.path.join(final_dir, "pages")
        os.makedirs(final_pages_dir, exist_ok=True)

        final_cover_rel = f"{book_id}/cover.jpg"
        final_cover_abs = os.path.join(BOOK_STORAGE_DIR, final_cover_rel)
        shutil.move(cover_tmp_path, final_cover_abs)

        # Matn sahifalar o'zgarishsiz qoladi, rasm sahifalar doimiy joyga ko'chiriladi
        final_page_items = []
        for i, item in enumerate(page_items, start=1):
            if item["type"] == "text":
                final_page_items.append({"type": "text", "content": item["content"]})
            else:
                src = item["path"]
                name = os.path.basename(src)
                dst_rel = f"{book_id}/pages/{name}"
                dst_abs = os.path.join(BOOK_STORAGE_DIR, dst_rel)
                shutil.move(src, dst_abs)
                final_page_items.append({"type": "image", "path": dst_rel})

        await db.set_book_cover_path(book_id, final_cover_rel)
        await db.add_book_pages_hybrid(book_id, final_page_items)

        shutil.rmtree(tmp_dir, ignore_errors=True)
        await state.clear()

        await message.answer(
            f"✅ Kitob muvaffaqiyatli qo'shildi!\n\n"
            f"📕 {title}\n"
            f"✍️ {author}\n"
            f"🏷 {genre}\n"
            f"📄 {len(final_page_items)} sahifa\n"
            f"👥 Kerak: {required_referrals} referral, 🪙 {coins_required} koin"
        )
    except Exception as e:
        logger.exception("Kitobni saqlashda xato")
        await message.answer(f"❌ Kitobni saqlashda xatolik yuz berdi: {e}")
        await state.clear()


# ---------- ADMIN: AUDIO KITOB QO'SHISH OQIMI ----------

MAX_AUDIO_TELEGRAM_DOWNLOAD_SIZE = 20 * 1024 * 1024  # Telegram Bot API cheklovi: 20 MB


@dp.message(Command("audio_kitob_qosh"))
async def cmd_add_audio_book(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    tmp_dir = os.path.join(BOOK_TMP_DIR, f"audio_{message.from_user.id}_{int(datetime.now().timestamp())}")
    os.makedirs(tmp_dir, exist_ok=True)

    await state.set_state(AddAudioBookStates.title)
    await state.update_data(tmp_dir=tmp_dir)
    await message.answer(
        "🎧 Yangi audio kitob qo'shamiz.\n\nKitob nomini yuboring "
        "(istalgan vaqt bekor qilish uchun /bekor):"
    )


@dp.message(AddAudioBookStates.title)
async def process_audio_book_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title:
        await message.answer("❗ Iltimos, matn ko'rinishida kitob nomini yuboring.")
        return
    await state.update_data(title=title)
    await state.set_state(AddAudioBookStates.author)
    await message.answer("✍️ Endi muallifining ismini yuboring:")


@dp.message(AddAudioBookStates.author)
async def process_audio_book_author(message: Message, state: FSMContext):
    author = (message.text or "").strip()
    if not author:
        await message.answer("❗ Iltimos, matn ko'rinishida muallif ismini yuboring.")
        return
    await state.update_data(author=author)
    await state.set_state(AddAudioBookStates.cover)
    await message.answer("🖼 Endi kitobning muqova rasmini yuboring (rasm sifatida):")


@dp.message(AddAudioBookStates.cover, F.photo)
async def process_audio_book_cover(message: Message, state: FSMContext):
    data = await state.get_data()
    tmp_dir = data["tmp_dir"]
    cover_path = os.path.join(tmp_dir, "cover.jpg")

    await bot.download(message.photo[-1], destination=cover_path)

    await state.update_data(cover_tmp_path=cover_path)
    await state.set_state(AddAudioBookStates.genre)
    await message.answer("🏷 Endi janrini yuboring (masalan: Roman, Diniy, Bolalar adabiyoti):")


@dp.message(AddAudioBookStates.cover)
async def process_audio_book_cover_invalid(message: Message):
    await message.answer("❗ Iltimos, muqova uchun rasm (📷) yuboring, matn emas.")


@dp.message(AddAudioBookStates.genre)
async def process_audio_book_genre(message: Message, state: FSMContext):
    genre = (message.text or "").strip()
    if not genre:
        await message.answer("❗ Iltimos, matn ko'rinishida janrini yuboring.")
        return
    await state.update_data(genre=genre, chapters=[])
    await state.set_state(AddAudioBookStates.chapters)
    await message.answer(
        "🎙 Endi audio bob fayllarini yuboring — <b>tartib bilan, 1-bobdan boshlab</b>, "
        "har birini alohida xabar qilib (musiqa/audio 🎵 yoki fayl 📎 sifatida, MP3).\n\n"
        "Har bobni yuborgandan so'ng \"Yana yuboring\" deb so'rayman. "
        "Hammasini yuborib bo'lgach <code>/tayyor</code> deb yozing.\n\n"
        "Bekor qilish uchun /bekor."
    )


async def _download_audio_chapter(message: Message, tmp_dir: str, chapter_index: int):
    """Audio, hujjat (mp3) yoki ovozli xabar (voice) dan bob faylini yuklab oladi.
    (path, title, duration_seconds) qaytaradi."""
    if message.audio:
        src = message.audio
        filename = src.file_name or f"{chapter_index}.mp3"
        duration = src.duration or 0
        title = src.title or (message.caption or "").strip() or f"{chapter_index}-bob"
    elif message.voice:
        src = message.voice
        filename = f"{chapter_index}.ogg"
        duration = src.duration or 0
        title = (message.caption or "").strip() or f"{chapter_index}-bob"
    elif message.document:
        src = message.document
        filename = src.file_name or f"{chapter_index}.mp3"
        mime_ok = (src.mime_type or "").startswith("audio/")
        ext_ok = filename.lower().endswith((".mp3", ".m4a", ".ogg", ".wav"))
        if not (mime_ok or ext_ok):
            return None
        duration = 0
        title = (message.caption or "").strip() or f"{chapter_index}-bob"
    else:
        return None

    if src.file_size and src.file_size > MAX_AUDIO_TELEGRAM_DOWNLOAD_SIZE:
        size_mb = src.file_size / (1024 * 1024)
        await message.answer(
            f"❌ Fayl juda katta ({size_mb:.1f} MB). Telegram bot API orqali faqat 20 MB "
            f"gacha bo'lgan fayllarni yuklab olish mumkin. Kichikroq bitrate bilan qayta eksport qilib yuboring."
        )
        return "too_big"

    ext = os.path.splitext(filename)[1] or ".mp3"
    dst = os.path.join(tmp_dir, f"chapter_{chapter_index}{ext}")
    await bot.download(src, destination=dst)
    return {"path": dst, "title": title, "duration_seconds": duration}


@dp.message(AddAudioBookStates.chapters, Command("tayyor"))
async def process_audio_book_chapters_done(message: Message, state: FSMContext):
    data = await state.get_data()
    chapters = data.get("chapters") or []
    if not chapters:
        await message.answer("❗ Hali birorta ham audio bob yuborilmadi. Kamida 1 ta bob yuboring, keyin /tayyor deb yozing.")
        return

    await state.set_state(AddAudioBookStates.referrals)
    await message.answer(
        f"✅ Jami {len(chapters)} ta bob qabul qilindi.\n\n"
        f"👥 Bu kitobni ochish uchun nechta referral (taklif qilingan do'st) kerak? "
        f"(shart bo'lmasa 0 yozing):"
    )


@dp.message(AddAudioBookStates.chapters, F.audio | F.voice | F.document)
async def process_audio_book_chapter_file(message: Message, state: FSMContext):
    data = await state.get_data()
    tmp_dir = data["tmp_dir"]
    chapters = data.get("chapters") or []
    chapter_index = len(chapters) + 1

    result = await _download_audio_chapter(message, tmp_dir, chapter_index)
    if result == "too_big":
        return
    if result is None:
        await message.answer("❗ Iltimos, audio (🎵), ovozli xabar (🎙) yoki MP3 fayl (📎) yuboring, yoki /tayyor / /bekor.")
        return

    chapters.append(result)
    await state.update_data(chapters=chapters)
    await message.answer(
        f"✅ {chapter_index}-bob qabul qilindi: <b>{html.escape(result['title'])}</b>\n"
        f"Yana bob yuboring, yoki hammasi bo'lsa <code>/tayyor</code> deb yozing."
    )


@dp.message(AddAudioBookStates.chapters)
async def process_audio_book_chapters_invalid(message: Message):
    await message.answer("❗ Iltimos, audio (🎵), ovozli xabar (🎙) yoki MP3 fayl (📎) yuboring, yoki /tayyor / /bekor.")


@dp.message(AddAudioBookStates.referrals)
async def process_audio_book_referrals(message: Message, state: FSMContext):
    try:
        referrals = int((message.text or "").strip())
        if referrals < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, butun son yuboring (masalan: 0 yoki 5).")
        return

    await state.update_data(required_referrals=referrals)
    await state.set_state(AddAudioBookStates.coins)
    await message.answer(
        "💵 Bu kitobning tannarxini dollarda yozing (masalan: 2 yoki 3.5).\n"
        "Agar kitob bepul (faqat referral orqali) bo'lsa — 0 yozing:"
    )


@dp.message(AddAudioBookStates.coins)
async def process_audio_book_coins(message: Message, state: FSMContext):
    try:
        cost_usd = float((message.text or "").strip().replace(",", "."))
        if cost_usd < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, musbat son yuboring (masalan: 0 yoki 2.5).")
        return

    coins_required = 0 if cost_usd == 0 else calculate_coin_price(cost_usd)

    data = await state.get_data()
    tmp_dir = data["tmp_dir"]
    title = data["title"]
    author = data["author"]
    genre = data["genre"]
    cover_tmp_path = data["cover_tmp_path"]
    required_referrals = data["required_referrals"]
    chapters = data["chapters"]

    await message.answer(
        f"✅ Hisoblandi: ${cost_usd:g} → <b>{coins_required:,}</b> koin\n\n💾 Saqlanmoqda..."
        if cost_usd > 0 else "💾 Saqlanmoqda..."
    )

    try:
        book_id = await db.add_book(
            title, author, genre, "", required_referrals, coins_required,
            cost_usd=cost_usd, book_type="audio",
        )

        final_dir = os.path.join(BOOK_STORAGE_DIR, str(book_id))
        final_audio_dir = os.path.join(final_dir, "audio")
        os.makedirs(final_audio_dir, exist_ok=True)

        final_cover_rel = f"{book_id}/cover.jpg"
        final_cover_abs = os.path.join(BOOK_STORAGE_DIR, final_cover_rel)
        shutil.move(cover_tmp_path, final_cover_abs)

        final_chapters = []
        for i, ch in enumerate(chapters, start=1):
            name = os.path.basename(ch["path"])
            dst_rel = f"{book_id}/audio/{name}"
            dst_abs = os.path.join(BOOK_STORAGE_DIR, dst_rel)
            shutil.move(ch["path"], dst_abs)
            final_chapters.append({
                "title": ch["title"], "path": dst_rel, "duration_seconds": ch["duration_seconds"],
            })

        await db.set_book_cover_path(book_id, final_cover_rel)
        await db.add_audio_chapters(book_id, final_chapters)

        shutil.rmtree(tmp_dir, ignore_errors=True)
        await state.clear()

        await message.answer(
            f"✅ Audio kitob muvaffaqiyatli qo'shildi!\n\n"
            f"🎧 {title}\n"
            f"✍️ {author}\n"
            f"🏷 {genre}\n"
            f"📼 {len(final_chapters)} bob\n"
            f"👥 Kerak: {required_referrals} referral, 🪙 {coins_required} koin"
        )
    except Exception as e:
        logger.exception("Audio kitobni saqlashda xato")
        await message.answer(f"❌ Audio kitobni saqlashda xatolik yuz berdi: {e}")
        await state.clear()


# ---------- ADMIN: VAZIFA QO'SHISH OQIMI ----------

@dp.message(Command("addtask"))
async def cmd_addtask(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    await state.set_state(AddTaskStates.title)
    await message.answer(
        "✅ Yangi vazifa qo'shamiz.\n\nVazifa nomini yozing "
        "(masalan: \"5 sahifa o'qing\") — istalgan vaqt bekor qilish uchun /bekor:"
    )


@dp.message(AddTaskStates.title)
async def process_task_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title:
        await message.answer("❗ Iltimos, matn ko'rinishida vazifa nomini yuboring.")
        return

    await state.update_data(title=title)
    await state.set_state(AddTaskStates.task_type)

    buttons = [
        [InlineKeyboardButton(text=label, callback_data=f"ttype_{key}")]
        for key, label in TASK_TYPE_LABELS.items()
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("🏷 Bu vazifa qaysi turga tegishli?", reply_markup=keyboard)


@dp.callback_query(AddTaskStates.task_type, F.data.startswith("ttype_"))
async def process_task_type(callback: CallbackQuery, state: FSMContext):
    task_type = callback.data.replace("ttype_", "")
    if task_type not in TASK_TYPE_LABELS:
        await callback.answer("Noto'g'ri tanlov", show_alert=True)
        return

    await state.update_data(task_type=task_type)
    await state.set_state(AddTaskStates.goal)
    await callback.message.edit_text(f"✅ Tanlandi: {TASK_TYPE_LABELS[task_type]}")
    await callback.message.answer(
        "🔢 Bu vazifa necha marta bajarilishi kerak? (masalan: 10):"
    )
    await callback.answer()


@dp.message(AddTaskStates.goal)
async def process_task_goal(message: Message, state: FSMContext):
    try:
        goal = int((message.text or "").strip())
        if goal <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, musbat butun son yuboring (masalan: 10).")
        return

    await state.update_data(goal=goal)
    await state.set_state(AddTaskStates.reward)
    await message.answer("🪙 Bu vazifa uchun necha koin mukofot beriladi?")


@dp.message(AddTaskStates.reward)
async def process_task_reward(message: Message, state: FSMContext):
    try:
        reward = int((message.text or "").strip())
        if reward <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, musbat butun son yuboring (masalan: 30).")
        return

    data = await state.get_data()
    title = data["title"]
    task_type = data["task_type"]
    goal = data["goal"]

    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:30] or "vazifa"
    code = f"{slug}_{int(datetime.now().timestamp())}"

    try:
        await db.add_task(code, title, task_type, goal, reward)
        await state.clear()
        await message.answer(
            f"✅ Yangi vazifa qo'shildi!\n\n"
            f"📌 {title}\n"
            f"🏷 {TASK_TYPE_LABELS[task_type]}\n"
            f"🎯 Maqsad: {goal}\n"
            f"🪙 Mukofot: {reward} koin"
        )
    except Exception as e:
        logger.exception("Vazifani saqlashda xato")
        await message.answer(f"❌ Xatolik yuz berdi: {e}")
        await state.clear()


# ---------- ADMIN: HAQIQIY DO'KON MAHSULOTI QO'SHISH OQIMI ----------

@dp.message(Command("addshop"))
async def cmd_addshop(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    tmp_dir = os.path.join(BOOK_TMP_DIR, f"shop_{message.from_user.id}_{int(datetime.now().timestamp())}")
    os.makedirs(tmp_dir, exist_ok=True)

    await state.set_state(AddShopProductStates.name)
    await state.update_data(tmp_dir=tmp_dir)
    await message.answer(
        "🛍 Do'konga yangi mahsulot qo'shamiz.\n\nMahsulot nomini yozing "
        "(masalan: \"Telegram Premium 1 oy\") — istalgan vaqt bekor qilish uchun /bekor:"
    )


@dp.message(AddShopProductStates.name)
async def process_shop_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("❗ Iltimos, matn ko'rinishida mahsulot nomini yuboring.")
        return
    await state.update_data(name=name)
    await state.set_state(AddShopProductStates.description)
    await message.answer(
        "📝 Endi qisqacha tavsif yozing (mahsulot rasmiga bosilganda shu matn ko'rinadi):"
    )


@dp.message(AddShopProductStates.description)
async def process_shop_description(message: Message, state: FSMContext):
    description = (message.text or "").strip()
    if not description:
        await message.answer("❗ Iltimos, tavsif matnini yuboring.")
        return
    await state.update_data(description=description)
    await state.set_state(AddShopProductStates.category)

    buttons = [
        [InlineKeyboardButton(text=label, callback_data=f"shopcat_{key}")]
        for key, label in SHOP_CATEGORY_LABELS.items()
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("🏷 Bu mahsulot qaysi turga tegishli?", reply_markup=keyboard)


@dp.callback_query(AddShopProductStates.category, F.data.startswith("shopcat_"))
async def process_shop_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.replace("shopcat_", "")
    if category not in SHOP_CATEGORY_LABELS:
        await callback.answer("Noto'g'ri tanlov", show_alert=True)
        return

    await state.update_data(category=category)
    await state.set_state(AddShopProductStates.payment_type)
    await callback.message.edit_text(f"✅ Tanlandi: {SHOP_CATEGORY_LABELS[category]}")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪙 Koin bilan", callback_data="paytype_coin")],
        [InlineKeyboardButton(text="💵 So'mda (UZS)", callback_data="paytype_uzs")],
    ])
    await callback.message.answer(
        "💰 Bu mahsulot qanday to'lanadi?\n\n"
        "🪙 <b>Koin bilan</b> — foydalanuvchi ilova ichida yig'gan koinlariga sotib oladi (avtomatik)\n"
        "💵 <b>So'mda (UZS)</b> — haqiqiy pul, siz admin sifatida qo'lda yakunlaysiz",
        reply_markup=keyboard,
    )
    await callback.answer()


@dp.callback_query(AddShopProductStates.payment_type, F.data.startswith("paytype_"))
async def process_shop_payment_type(callback: CallbackQuery, state: FSMContext):
    payment_type = callback.data.replace("paytype_", "")
    if payment_type not in ("coin", "uzs"):
        await callback.answer("Noto'g'ri tanlov", show_alert=True)
        return

    await state.update_data(payment_type=payment_type)
    await state.set_state(AddShopProductStates.image)
    label = "🪙 Koin bilan" if payment_type == "coin" else "💵 So'mda (UZS)"
    await callback.message.edit_text(f"✅ To'lov turi: {label}")
    await callback.message.answer("🖼 Endi mahsulot rasmini yuboring (rasm sifatida):")
    await callback.answer()


@dp.message(AddShopProductStates.image, F.photo)
async def process_shop_image(message: Message, state: FSMContext):
    data = await state.get_data()
    tmp_dir = data["tmp_dir"]
    image_path = os.path.join(tmp_dir, "shop_image.jpg")
    await bot.download(message.photo[-1], destination=image_path)

    await state.update_data(image_tmp_path=image_path)

    if data.get("payment_type") == "uzs":
        await state.set_state(AddShopProductStates.price_uzs)
        await message.answer("💵 Mahsulot narxini so'mda yozing (masalan: 150000):")
    else:
        await state.set_state(AddShopProductStates.cost_usd)
        await message.answer(
            "💵 Mahsulotning tannarxini yozing (dollarda, masalan: 3 yoki 4.5):\n\n"
            f"ℹ️ Bot avtomatik {DEFAULT_MARGIN_PERCENT}% marja qo'shib, Koin narxini hisoblab qo'yadi."
        )


@dp.message(AddShopProductStates.price_uzs)
async def process_shop_price_uzs(message: Message, state: FSMContext):
    try:
        price_uzs = int((message.text or "").strip().replace(" ", "").replace(",", ""))
        if price_uzs <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, musbat butun son yuboring (masalan: 150000).")
        return

    await state.update_data(price_uzs=price_uzs, cost=0, cost_usd=0)
    await state.set_state(AddShopProductStates.referrals)
    await message.answer(
        f"✅ Narx: {price_uzs:,} so'm\n\n"
        "👥 Sotib olish uchun nechta referral talab qilinsin? (shart bo'lmasa 0 yozing):"
    )


@dp.message(AddShopProductStates.image)
async def process_shop_image_invalid(message: Message):
    await message.answer("❗ Iltimos, rasm yuboring (📷), yoki /bekor bilan bekor qiling.")


@dp.message(AddShopProductStates.cost_usd)
async def process_shop_cost_usd(message: Message, state: FSMContext):
    try:
        cost_usd = float((message.text or "").strip().replace(",", "."))
        if cost_usd <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, musbat son yuboring (masalan: 3 yoki 4.5).")
        return

    coin_price = calculate_coin_price(cost_usd)
    await state.update_data(cost_usd=cost_usd, cost=coin_price, margin_percent=DEFAULT_MARGIN_PERCENT)
    await state.set_state(AddShopProductStates.referrals)
    await message.answer(
        f"✅ Hisoblandi: ${cost_usd:g} → <b>{coin_price:,}</b> koin (marja: {DEFAULT_MARGIN_PERCENT}%)\n\n"
        "👥 Sotib olish uchun nechta referral talab qilinsin? (shart bo'lmasa 0 yozing):"
    )


@dp.message(AddShopProductStates.referrals)
async def process_shop_referrals(message: Message, state: FSMContext):
    try:
        referrals = int((message.text or "").strip())
        if referrals < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, butun son yuboring (masalan: 0 yoki 10).")
        return

    await state.update_data(required_referrals=referrals)
    await state.set_state(AddShopProductStates.stock)
    await message.answer("📦 Nechta dona bor? (cheksiz bo'lsa 0 yozing):")


@dp.message(AddShopProductStates.stock)
async def process_shop_stock(message: Message, state: FSMContext):
    try:
        stock_input = int((message.text or "").strip())
        if stock_input < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, butun son yuboring (masalan: 0 yoki 20).")
        return

    stock = None if stock_input == 0 else stock_input

    data = await state.get_data()
    tmp_dir = data["tmp_dir"]
    payment_type = data.get("payment_type", "coin")

    try:
        product_id = await db.add_shop_product(
            data["name"], data["description"], data["category"],
            data["cost"], data["required_referrals"], stock,
            cost_usd=data.get("cost_usd", 0),
            payment_type=payment_type,
            price_uzs=data.get("price_uzs"),
        )

        final_dir = os.path.join(BOOK_STORAGE_DIR, "shop", str(product_id))
        os.makedirs(final_dir, exist_ok=True)
        final_rel = f"shop/{product_id}/image.jpg"
        final_abs = os.path.join(BOOK_STORAGE_DIR, final_rel)
        shutil.move(data["image_tmp_path"], final_abs)
        await db.set_shop_product_image_path(product_id, final_rel)

        shutil.rmtree(tmp_dir, ignore_errors=True)
        await state.clear()

        stock_display = "Cheksiz" if stock is None else str(stock)
        if payment_type == "uzs":
            price_line = f"💵 Narxi: {data.get('price_uzs', 0):,} so'm"
        else:
            price_line = (
                f"💵 Tannarx: ${data.get('cost_usd', 0):g}\n"
                f"🪙 Narxi (avtomatik hisoblandi): {data['cost']:,} koin"
            )
        await message.answer(
            f"✅ Mahsulot qo'shildi!\n\n"
            f"🛍 {data['name']}\n"
            f"🏷 {SHOP_CATEGORY_LABELS[data['category']]}\n"
            f"{price_line}\n"
            f"👥 Kerakli referral: {data['required_referrals']}\n"
            f"📦 Dona: {stock_display}"
        )
    except Exception as e:
        logger.exception("Mahsulotni saqlashda xato")
        await message.answer(f"❌ Xatolik yuz berdi: {e}")
        await state.clear()


@dp.message(Command("dokon_stok"))
async def cmd_dokon_stok(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat administratorlar uchun.")
        return

    parts = message.text.split()
    if len(parts) != 3:
        await message.answer(
            "❗ Foydalanish:\n"
            "<code>/dokon_stok 3 20</code> — 3-ID'li mahsulotga 20 dona qo'shish\n"
            "<code>/dokon_stok 3 0</code> — cheksiz qilib belgilash"
        )
        return

    try:
        product_id = int(parts[1])
        new_stock_input = int(parts[2])
        if new_stock_input < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ ID va son butun son bo'lishi kerak.")
        return

    product = await db.get_shop_product(product_id)
    if not product:
        await message.answer("❗ Bunday ID'li mahsulot topilmadi.")
        return

    new_stock = None if new_stock_input == 0 else new_stock_input
    await db.restock_shop_product(product_id, new_stock)
    stock_display = "Cheksiz" if new_stock is None else str(new_stock)
    await message.answer(f"✅ \"{product['name']}\" uchun yangi dona soni: {stock_display}")


@dp.message(Command("buyurtmalar"))
async def cmd_buyurtmalar(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat administratorlar uchun.")
        return

    orders = await db.get_recent_shop_orders(30)
    if not orders:
        await message.answer("Hozircha buyurtmalar yo'q.")
        return

    lines = ["📦 <b>So'nggi buyurtmalar</b>\n"]
    for o in orders:
        name = o["username"] or str(o["user_id"])
        lines.append(
            f"#{o['id']} — 👤 {name} (ID: {o['user_id']})\n"
            f"   🛍 {o['product_name']} — 🪙{o['cost_paid']} — {o['status']}\n"
            f"   📅 {o['purchased_at']}"
        )

    text = "\n\n".join(lines)
    for i in range(0, len(text), 3500):
        await message.answer(text[i:i + 3500])


# ---------- ADMIN: TEST (QUIZ) QO'SHISH OQIMI ----------

def _parse_quiz_file(text: str):
    """Admin yuborgan .txt fayldagi savollarni ajratib oladi.
    Format:
        Savol matni?
        A) variant
        B) variant
        C) variant
        D) variant
        Javob: B
    "Javob:" qatori chegara sifatida ishlatiladi — savollar orasida (yoki savol
    bilan variantlar orasida) qancha bo'sh qator bo'lishidan qat'i nazar, bo'sh
    qatorlar shunchaki e'tiborsiz qoldiriladi (chalkashlikka olib kelmaydi).
    Muvaffaqiyatli o'qilgan savollar ro'yxatini va xato xabarlarini qaytaradi."""
    questions = []
    errors = []
    lines = [l.strip() for l in text.split("\n")]

    current_question_lines = []
    current_options = {}
    block_num = 0

    for line in lines:
        if not line:
            continue

        m = re.match(r"^([A-Da-d])[\.\)]\s*(.+)$", line)
        if m:
            current_options[m.group(1).upper()] = m.group(2).strip()
            continue

        m2 = re.match(r"^(?:javob|to'g'ri javob|to'g'ri|togri|javobi)\s*:?\s*([A-Da-d])\s*$", line, re.IGNORECASE)
        if m2:
            correct = m2.group(1).upper()
            question_text = " ".join(current_question_lines).strip()
            question_text = re.sub(r"^\d+[\.\)]\s*", "", question_text)
            block_num += 1
            if question_text and all(k in current_options for k in ("A", "B", "C", "D")):
                questions.append({
                    "question": question_text,
                    "a": current_options["A"], "b": current_options["B"],
                    "c": current_options["C"], "d": current_options["D"],
                    "correct": correct,
                })
            else:
                errors.append(
                    f"{block_num}-savol: to'liq emas (savol matni yoki "
                    f"A/B/C/D variantlardan biri yetishmayapti)"
                )
            current_question_lines = []
            current_options = {}
            continue

        if not current_options:
            current_question_lines.append(line)

    if current_question_lines or current_options:
        block_num += 1
        errors.append(f"{block_num}-savol: \"Javob:\" qatori topilmadi, savol o'tkazib yuborildi")

    return questions, errors


def _parse_challenge_file(text: str):
    """Challenge uchun to'liq fayl formatini o'qiydi:
    KITOB: ...
    TAVSIF: ...
    KUNLAR: 7
    OTISH: 80

    ===KUN1===
    MATN: ...
    (savollar, _parse_quiz_file formatida)

    ===FINAL===
    (savollar)

    ===TOP5===
    1: coin xp
    2: coin xp
    ...
    """
    result = {
        "book_title": None, "description": "", "days_count": 0, "pass_percent": 80,
        "days": {}, "final_questions": [], "top_bonuses": [], "errors": [],
    }

    header_match = re.search(r"^KITOB:\s*(.+)$", text, re.MULTILINE)
    if header_match:
        result["book_title"] = header_match.group(1).strip()

    desc_match = re.search(r"^TAVSIF:\s*(.+)$", text, re.MULTILINE)
    if desc_match:
        result["description"] = desc_match.group(1).strip()

    days_match = re.search(r"^KUNLAR:\s*(\d+)$", text, re.MULTILINE)
    if days_match:
        result["days_count"] = int(days_match.group(1))

    pass_match = re.search(r"^OTISH:\s*(\d+)$", text, re.MULTILINE)
    if pass_match:
        result["pass_percent"] = int(pass_match.group(1))

    # Bo'limlarni ajratish: ===KUN1===, ===KUN2===, ===FINAL===, ===TOP5===
    sections = re.split(r"===\s*(KUN\d+|FINAL|TOP5)\s*===", text)
    # sections[0] = header qismi, keyin juft-juft: nom, matn

    for i in range(1, len(sections), 2):
        section_name = sections[i].strip().upper()
        section_body = sections[i + 1] if i + 1 < len(sections) else ""

        if section_name.startswith("KUN"):
            day_num = int(section_name.replace("KUN", ""))
            matn_match = re.search(r"^MATN:\s*(.+)$", section_body, re.MULTILINE)
            reading_text = matn_match.group(1).strip() if matn_match else ""
            questions_body = re.sub(r"^MATN:.*$", "", section_body, count=1, flags=re.MULTILINE)
            questions, errors = _parse_quiz_file(questions_body)
            result["days"][day_num] = {"reading_text": reading_text, "questions": questions}
            result["errors"].extend([f"KUN{day_num}: {e}" for e in errors])

        elif section_name == "FINAL":
            questions, errors = _parse_quiz_file(section_body)
            result["final_questions"] = questions
            result["errors"].extend([f"FINAL: {e}" for e in errors])

        elif section_name == "TOP5":
            for line in section_body.strip().split("\n"):
                line = line.strip()
                m = re.match(r"^(\d+)\s*:\s*(\d+)\s+(\d+)$", line)
                if m:
                    result["top_bonuses"].append({
                        "rank": int(m.group(1)), "coin": int(m.group(2)), "xp": int(m.group(3)),
                    })

    return result


CHALLENGE_QUESTION_TIMER = 10
CHALLENGE_COIN_PER_CORRECT = 3
CHALLENGE_XP_PER_CORRECT = 2
FINAL_TEST_QUESTION_TIMER = 15
FINAL_TEST_COIN_PER_CORRECT = 3
FINAL_TEST_XP_PER_CORRECT = 2
CHALLENGE_DAY_UNLOCK_HOURS = 24

# Challenge'ga qo'shilish narxi (koinda)
CHALLENGE_JOIN_COST = 17

# Final testda pass_percent (masalan 70%) dan ko'p to'plaganlarga beriladigan
# BIR MARTALIK qo'shimcha bonus (har to'g'ri javob uchun beriladigan
# FINAL_TEST_COIN_PER_CORRECT/XP'ga QO'SHIMCHA ravishda beriladi)
FINAL_PASS_BONUS_COINS = 500
FINAL_PASS_BONUS_XP = 50


@dp.message(Command("challenge_yarat"))
async def cmd_challenge_yarat(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(ChallengeCreateStates.cover)
    await message.answer(
        "📚 Yangi Challenge yaratamiz.\n\n"
        "Avval banner rasmini yuboring (kitob muqovasi yoki barcha ma'lumotli "
        "umumiy banner — ikkalasi ham bo'ladi, davomiylik/mukofotlar matn "
        "ko'rinishida alohida ham chiqadi):"
    )


@dp.message(ChallengeCreateStates.cover, F.photo)
async def process_challenge_cover(message: Message, state: FSMContext):
    tmp_dir = os.path.join(BOOK_TMP_DIR, f"challenge_{message.from_user.id}_{int(datetime.now().timestamp())}")
    os.makedirs(tmp_dir, exist_ok=True)
    cover_path = os.path.join(tmp_dir, "cover.jpg")
    await bot.download(message.photo[-1], destination=cover_path)
    await state.update_data(tmp_dir=tmp_dir, cover_tmp_path=cover_path)
    await state.set_state(ChallengeCreateStates.file)
    await message.answer(
        "✅ Rasm qabul qilindi!\n\n"
        "Endi to'liq Challenge faylini (.txt) yuboring. Format:\n\n"
        "<code>KITOB: Kitob nomi\n"
        "TAVSIF: Qisqacha tavsif\n"
        "KUNLAR: 7\n"
        "OTISH: 70\n\n"
        "===KUN1===\n"
        "MATN: 1-qismni o'qing\n"
        "Savol matni?\n"
        "A) variant\n"
        "B) variant\n"
        "C) variant\n"
        "D) variant\n"
        "Javob: B\n"
        "(yana 6 ta savol...)\n\n"
        "===KUN2===\n"
        "...\n\n"
        "===FINAL===\n"
        "(50 ta savol)</code>\n\n"
        f"ℹ️ Challenge'ga qo'shilish {CHALLENGE_JOIN_COST} koin, kunlik to'g'ri javob uchun "
        f"{CHALLENGE_COIN_PER_CORRECT} koin/{CHALLENGE_XP_PER_CORRECT} XP, final to'g'ri javob uchun "
        f"{FINAL_TEST_COIN_PER_CORRECT} koin/{FINAL_TEST_XP_PER_CORRECT} XP, va OTISH% dan ko'p "
        f"to'plasa qo'shimcha +{FINAL_PASS_BONUS_COINS} koin/+{FINAL_PASS_BONUS_XP} XP avtomatik beriladi "
        f"(TOP5 bo'limi endi kerak emas)."
    )


@dp.message(ChallengeCreateStates.cover)
async def process_challenge_cover_invalid(message: Message):
    await message.answer("❗ Iltimos, rasm yuboring (📷), yoki /bekor bilan bekor qiling.")


@dp.message(ChallengeCreateStates.file, F.document)
async def process_challenge_file(message: Message, state: FSMContext):
    doc = message.document
    filename = (doc.file_name or "").lower()
    if not filename.endswith(".txt"):
        await message.answer("❗ Iltimos, .txt formatidagi fayl yuboring.")
        return

    data = await state.get_data()
    tmp_path = os.path.join(data["tmp_dir"], "challenge.txt")
    await bot.download(doc, destination=tmp_path)

    try:
        with open(tmp_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(tmp_path, "r", encoding="utf-8-sig", errors="replace") as f:
            content = f.read()

    parsed = _parse_challenge_file(content)

    if not parsed["book_title"]:
        await message.answer("❌ 'KITOB:' qatori topilmadi. Faylni tekshirib qaytadan yuboring.")
        return
    if not parsed["days"]:
        await message.answer("❌ Hech qanday 'KUN' bo'limi topilmadi. Faylni tekshirib qaytadan yuboring.")
        return
    if not parsed["final_questions"]:
        await message.answer("❌ 'FINAL' bo'limi topilmadi yoki bo'sh. Faylni tekshirib qaytadan yuboring.")
        return

    final_dir = os.path.join(BOOK_STORAGE_DIR, "challenges")
    os.makedirs(final_dir, exist_ok=True)

    challenge_id = await db.create_challenge(
        parsed["book_title"], "", parsed["description"],
        parsed["days_count"] or len(parsed["days"]), parsed["pass_percent"],
    )

    cover_rel = f"challenges/{challenge_id}_cover.jpg"
    cover_abs = os.path.join(BOOK_STORAGE_DIR, cover_rel)
    shutil.move(data["cover_tmp_path"], cover_abs)
    async with db._pool.acquire() as conn:
        await conn.execute("UPDATE challenges SET cover_path = $2 WHERE id = $1", challenge_id, cover_rel)

    for day_num, day_data in parsed["days"].items():
        await db.add_challenge_day(challenge_id, day_num, day_data["reading_text"])
        await db.add_challenge_questions(challenge_id, day_num, day_data["questions"])

    await db.add_challenge_questions(challenge_id, None, parsed["final_questions"])

    # Eslatma: TOP5 reyting bo'yicha alohida bonus endi ishlatilmaydi — mukofot
    # (OTISH% dan ko'p to'plagan HAR BIR kishiga +500 koin/+50 XP) endi
    # api_challenge_final_submit orqali avtomatik va bir xilda beriladi.

    shutil.rmtree(data["tmp_dir"], ignore_errors=True)
    await state.clear()

    total_day_questions = sum(len(d["questions"]) for d in parsed["days"].values())
    result_text = (
        f"✅ Challenge yaratildi!\n\n"
        f"📚 {parsed['book_title']}\n"
        f"📅 {len(parsed['days'])} kun, {total_day_questions} ta kunlik savol\n"
        f"🏆 Final: {len(parsed['final_questions'])} ta savol\n"
        f"🎓 O'tish balli: {parsed['pass_percent']}%\n"
        f"Challenge ID: {challenge_id}"
    )
    if parsed["errors"]:
        result_text += f"\n\n⚠️ {len(parsed['errors'])} ta xato:\n" + "\n".join(parsed["errors"][:8])
    await message.answer(result_text)


@dp.message(ChallengeCreateStates.file)
async def process_challenge_file_invalid(message: Message):
    await message.answer("❗ Iltimos, .txt faylni hujjat (📎) sifatida yuboring, yoki /bekor.")


@dp.message(Command("challenge_yakunlash"))
async def cmd_challenge_yakunlash(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        active = await db.get_active_challenge()
        if not active:
            await message.answer("Faol challenge topilmadi.")
            return
        challenge_id = active["id"]
    else:
        challenge_id = int(parts[1])

    challenge = await db.get_challenge(challenge_id)
    if not challenge:
        await message.answer("Challenge topilmadi.")
        return

    # Eslatma: mukofot (70%+ uchun +500 koin/+50 XP) endi TOP5 reyting bo'yicha
    # emas, balki har bir ishtirokchiga final testni topshirgan zahoti avtomatik
    # beriladi (qarang: api_challenge_final_submit). Bu yerda challenge'ni yopib,
    # ISHONCHLILIK uchun 70%+ to'plagan (status='completed') BARCHA
    # ishtirokchilarning yakuniy hisobotini e'lon qilamiz — qayta pul tarqatilmaydi.
    passed_list = await db.get_challenge_leaderboard(challenge_id, limit=100000)
    total_participants = await db.get_challenge_participant_count(challenge_id)

    await db.finish_challenge(challenge_id)

    passed_count = len(passed_list)

    if passed_list:
        lines = []
        for idx, p in enumerate(passed_list, start=1):
            name = p["username"] or str(p["user_id"])
            lines.append(f"{idx}. {name} — {float(p['final_percent'])}%")
        # Telegram xabar hajmi cheklangani uchun juda uzun ro'yxatni bo'laklarga bo'lamiz
        MAX_NAMES_PER_MSG = 60
        header = (
            f"✅ \"{challenge['book_title']}\" challenge'i yakunlandi!\n\n"
            f"📊 <b>Yakuniy hisobot:</b> {total_participants} kishi ishtirok etdi, "
            f"{passed_count} kishi {challenge['pass_percent']}%+ to'plab o'tdi.\n\n"
            f"🏆 <b>O'tganlar ro'yxati:</b>\n"
        )
        await message.answer(header + "\n".join(lines[:MAX_NAMES_PER_MSG]))
        remaining = lines[MAX_NAMES_PER_MSG:]
        while remaining:
            chunk, remaining = remaining[:MAX_NAMES_PER_MSG], remaining[MAX_NAMES_PER_MSG:]
            await message.answer("\n".join(chunk))
        await message.answer(
            f"ℹ️ Ularning barchasi mukofotini (+{FINAL_PASS_BONUS_COINS} koin / +{FINAL_PASS_BONUS_XP} XP) "
            f"testni topshirgan paytida avtomatik olib bo'lishgan."
        )
    else:
        await message.answer(
            f"✅ \"{challenge['book_title']}\" challenge'i yakunlandi!\n\n"
            f"📊 {total_participants} kishi ishtirok etdi, lekin hech kim "
            f"{challenge['pass_percent']}%+ to'play olmadi."
        )


@dp.message(F.text.regexp(r"^/challenge_ochir\s+(\d+)$"))
async def cmd_challenge_ochir(message: Message):
    if not is_admin(message.from_user.id):
        return
    match = re.match(r"^/challenge_ochir\s+(\d+)$", message.text)
    challenge_id = int(match.group(1))

    challenge = await db.get_challenge(challenge_id)
    if not challenge:
        await message.answer("❗ Bunday ID'li challenge topilmadi.")
        return

    deleted = await db.delete_challenge(challenge_id)
    if deleted:
        await message.answer(
            f"✅ \"{challenge['book_title']}\" (ID: {challenge_id}) challenge'i va unga bog'liq "
            f"barcha ma'lumotlar (kunlar, savollar, ishtirokchilar, sertifikatlar) butunlay o'chirildi."
        )
    else:
        await message.answer("❗ O'chirishda xatolik yuz berdi.")


@dp.message(Command("addquiz"))
async def cmd_addquiz(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    await state.set_state(AddQuizStates.title)
    await message.answer(
        "🧠 Yangi test yaratamiz.\n\nTest nomini yozing (masalan: \"Sariq devni minib\") "
        "— istalgan vaqt bekor qilish uchun /bekor:"
    )


@dp.message(AddQuizStates.title)
async def process_quiz_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title:
        await message.answer("❗ Iltimos, matn ko'rinishida test nomini yuboring.")
        return
    await state.update_data(title=title)
    await state.set_state(AddQuizStates.referrals)
    await message.answer("👥 Testga kirish uchun nechta referral kerak? (shart bo'lmasa 0 yozing):")


@dp.message(AddQuizStates.referrals)
async def process_quiz_referrals(message: Message, state: FSMContext):
    try:
        referrals = int((message.text or "").strip())
        if referrals < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, butun son yuboring (masalan: 0 yoki 13).")
        return

    await state.update_data(required_referrals=referrals)
    await state.set_state(AddQuizStates.file)
    await message.answer(
        "📄 Endi savollar faylini (.txt) yuboring.\n\n"
        "Format (har savolni bo'sh qator bilan ajrating):\n\n"
        "<code>Savol matni?\n"
        "A) variant 1\n"
        "B) variant 2\n"
        "C) variant 3\n"
        "D) variant 4\n"
        "Javob: B</code>"
    )


@dp.message(AddQuizStates.file, F.document)
async def process_quiz_file(message: Message, state: FSMContext):
    doc = message.document
    filename = (doc.file_name or "").lower()
    if not filename.endswith(".txt"):
        await message.answer("❗ Iltimos, .txt formatidagi fayl yuboring.")
        return

    tmp_path = os.path.join(BOOK_TMP_DIR, f"quiz_{message.from_user.id}_{int(datetime.now().timestamp())}.txt")
    await bot.download(doc, destination=tmp_path)

    try:
        with open(tmp_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(tmp_path, "r", encoding="utf-8-sig", errors="replace") as f:
            content = f.read()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    questions, errors = _parse_quiz_file(content)

    if not questions:
        error_text = "\n".join(errors[:10]) if errors else "Fayldan savol topilmadi."
        await message.answer(
            f"❌ Hech qanday savol o'qib bo'lmadi.\n\n{error_text}\n\n"
            f"Formatni tekshirib, qaytadan yuboring, yoki /bekor bilan to'xtating."
        )
        return

    data = await state.get_data()
    quiz_id = await db.add_quiz_set(
        data["title"], data["required_referrals"],
        reward_per_correct=3, question_timer_seconds=20,
    )
    await db.add_quiz_questions(quiz_id, questions)
    await state.clear()

    result_text = f"✅ Test yaratildi!\n\n🧠 {data['title']}\n📄 {len(questions)} ta savol qo'shildi\n"
    if data["required_referrals"] > 0:
        result_text += f"👥 Kerakli referral: {data['required_referrals']}\n"
    if errors:
        result_text += f"\n⚠️ {len(errors)} ta blok o'qib bo'lmadi:\n" + "\n".join(errors[:5])

    await message.answer(result_text)


@dp.message(AddQuizStates.file)
async def process_quiz_file_invalid(message: Message):
    await message.answer("❗ Iltimos, .txt faylni hujjat (📎) sifatida yuboring, yoki /bekor.")


# ================== API (Mini App) ==================

async def api_me(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    username = user_data.get("username") or user_data.get("first_name", "User")
    await db.create_user_if_missing(user_id, username)
    user = await db.get_user(user_id)
    is_premium = await db.check_premium_status(user_id)
    streak_info = await db.update_streak(user_id)
    streak = streak_info["streak"]

    streak_bonus = get_streak_bonus_amount(streak)
    granted = await db.try_grant_streak_bonus(user_id, streak_bonus)

    daily_granted = 0
    if is_premium:
        daily_granted = await db.try_grant_daily_bonus(user_id, DAILY_BONUS_PREMIUM)

    referral_count = await db.get_referral_count(user_id)

    return web.json_response({
        "user_id": user_id,
        "username": username,
        "coins": user["coins"],
        "is_premium": is_premium,
        "premium_expires": user["premium_expires_at"].isoformat() if user["premium_expires_at"] else None,
        "pages_read": user["pages_read"] or 0,
        "streak_days": streak,
        "streak_freeze_used": streak_info["freeze_used"],
        "streak_freezes_left": streak_info["freezes_left"],
        "has_seen_onboarding": user["has_seen_onboarding"] or False,
        "referral_count": referral_count,
        "streak_bonus_granted": granted,
        "daily_bonus_granted": daily_granted,
        "milestone_pages": MILESTONE_PAGES,
        "milestone_bonus": MILESTONE_BONUS_PREMIUM if is_premium else MILESTONE_BONUS,
        "avatar_url": f"/api/avatar/{user_id}" if user["avatar_path"] else None,
        "level": user["level"] or 1,
    })


async def api_shop_items(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    is_premium = await db.check_premium_status(user_id)
    user = await db.get_user(user_id)

    items = []
    for item_id, item in SHOP_ITEMS.items():
        owned = await db.is_item_owned(user_id, item_id)
        cost = item["cost"]
        if is_premium:
            cost = int(cost * (1 - SHOP_DISCOUNT_PREMIUM))
        items.append({
            "id": item_id,
            "name": item["name"],
            "emoji": item["emoji"],
            "cost": cost,
            "original_cost": item["cost"],
            "type": item["type"],
            "premium_only": item["premium_only"],
            "owned": owned,
            "can_buy": (not owned) and user["coins"] >= cost and (not item["premium_only"] or is_premium)
        })

    return web.json_response({"items": items, "coins": user["coins"], "is_premium": is_premium})


async def api_premium_info(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    user = await db.get_user(user_id)
    is_premium = await db.check_premium_status(user_id)

    days_left = None
    if is_premium and user["premium_expires_at"]:
        remaining = user["premium_expires_at"] - datetime.now()
        days_left = max(0, remaining.days + (1 if remaining.seconds > 0 else 0))

    tiers = []
    for tid, t in PREMIUM_TIERS.items():
        tiers.append({
            "id": tid,
            "name": t["name"],
            "emoji": t["emoji"],
            "days": t["days"],
            "base_cost": t["base_cost"],
            "discount_pct": int(t["discount"] * 100),
            "final_cost": premium_tier_final_cost(tid),
        })

    return web.json_response({
        "coins": user["coins"],
        "is_premium": is_premium,
        "days_left": days_left,
        "total_premium_days": user["total_premium_days"] or 0,
        "tiers": tiers,
        "quiz_reward": QUIZ_REWARD_PER_CORRECT,
        "quiz_reward_premium": QUIZ_REWARD_PER_CORRECT_PREMIUM,
        "milestone_bonus": MILESTONE_BONUS,
        "milestone_bonus_premium": MILESTONE_BONUS_PREMIUM,
        "shop_discount_pct": int(SHOP_DISCOUNT_PREMIUM * 100),
        "book_discount_pct": int(BOOK_COIN_DISCOUNT_PREMIUM * 100),
    })


async def api_buy_premium_tier(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    tier_id = body.get("tier")
    if tier_id not in PREMIUM_TIERS:
        return web.json_response({"error": "invalid_tier"}, status=400)

    is_premium = await db.check_premium_status(user_id)
    if is_premium:
        return web.json_response({"error": "already_premium"}, status=400)

    tier = PREMIUM_TIERS[tier_id]
    cost = premium_tier_final_cost(tier_id)

    user = await db.get_user(user_id)
    if user["coins"] < cost:
        return web.json_response({"error": "not_enough_coins"}, status=400)

    new_balance = await db.add_coins(user_id, -cost)
    await db.activate_premium(user_id, days=tier["days"])
    await db.add_purchase(user_id, f"premium_{tier_id}", f"{tier['name']} ({tier['days']} kun Premium)", tier["emoji"], "premium")

    return web.json_response({
        "success": True,
        "coins": new_balance,
        "days": tier["days"],
        "tier_name": tier["name"],
    })


async def api_purchase(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    item_id = body.get("item_id")
    if item_id not in SHOP_ITEMS:
        return web.json_response({"error": "invalid item"}, status=400)

    item = SHOP_ITEMS[item_id]
    is_premium = await db.check_premium_status(user_id)
    user = await db.get_user(user_id)

    if await db.is_item_owned(user_id, item_id):
        return web.json_response({"error": "already_owned"}, status=400)
    if item["premium_only"] and not is_premium:
        return web.json_response({"error": "premium_required"}, status=400)

    cost = item["cost"]
    if is_premium:
        cost = int(cost * (1 - SHOP_DISCOUNT_PREMIUM))

    if user["coins"] < cost:
        return web.json_response({"error": "not_enough_coins"}, status=400)

    new_balance = await db.add_coins(user_id, -cost)
    await db.add_purchase(user_id, item_id, item["name"], item["emoji"], item["type"])

    if item["type"] == "premium":
        await db.activate_premium(user_id, days=PREMIUM_DURATION_DAYS)

    return web.json_response({
        "success": True,
        "coins": new_balance,
        "item_name": item["name"]
    })


# ---------- HAQIQIY DO'KON (jismoniy mahsulotlar) API ----------

async def api_shop_products(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    await db.create_user_if_missing(user_id, user_data.get("username") or user_data.get("first_name", "User"))

    products = await db.get_active_shop_products()
    referral_count = await db.get_referral_count(user_id)
    user = await db.get_user(user_id)

    result = []
    for p in products:
        stock = p["stock"]
        in_stock = stock is None or stock > 0
        can_buy = (
            in_stock
            and referral_count >= p["required_referrals"]
            and user["coins"] >= p["cost"]
        )
        result.append({
            "id": p["id"],
            "name": p["name"],
            "description": p["description"],
            "category": p["category"],
            "category_label": SHOP_CATEGORY_LABELS.get(p["category"], p["category"]),
            "image_url": f"/api/shop_image/{p['id']}" if p["image_path"] else None,
            "cost": p["cost"],
            "cost_uzs": usd_to_uzs(p["cost_usd"] if "cost_usd" in p.keys() else 0),
            "payment_type": p["payment_type"] if "payment_type" in p.keys() and p["payment_type"] else "coin",
            "price_uzs": p["price_uzs"] if "price_uzs" in p.keys() else None,
            "required_referrals": p["required_referrals"],
            "stock": stock,
            "in_stock": in_stock,
            "can_buy": can_buy,
        })

    return web.json_response({
        "products": result,
        "coins": user["coins"],
        "referral_count": referral_count,
    })


async def api_shop_image(request: web.Request):
    product_id = int(request.match_info["product_id"])
    product = await db.get_shop_product(product_id)
    if not product or not product["image_path"]:
        return web.Response(status=404)
    full_path = os.path.join(BOOK_STORAGE_DIR, product["image_path"])
    if not os.path.exists(full_path):
        return web.Response(status=404)
    return web.FileResponse(full_path)


async def api_buy_shop_product(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    product_id = body.get("product_id")
    product = await db.get_shop_product(product_id)
    if not product or not product["active"]:
        return web.json_response({"error": "invalid_product"}, status=400)

    stock = product["stock"]
    if stock is not None and stock <= 0:
        return web.json_response({"error": "out_of_stock"}, status=400)

    referral_count = await db.get_referral_count(user_id)
    if referral_count < product["required_referrals"]:
        return web.json_response({
            "error": "not_enough_referrals",
            "required_referrals": product["required_referrals"],
            "current_referrals": referral_count,
        }, status=400)

    payment_type = product["payment_type"] if product["payment_type"] else "coin"

    if payment_type == "uzs":
        user = await db.get_user(user_id)
        order_id = await db.record_shop_purchase(
            user_id, product_id, product["price_uzs"] or 0,
            payment_type="uzs",
            contact_phone=user["phone"] if user else None,
            contact_username=user["username"] if user else None,
        )
        loyalty_bonus = await apply_shop_loyalty_bonus(user_id)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"💵 Yangi buyurtma (SO'MDA)!\n\n"
                    f"🛍 {product['name']}\n"
                    f"💰 Narxi: {(product['price_uzs'] or 0):,} so'm\n"
                    f"👤 Xaridor: @{user['username'] if user and user['username'] else '—'} "
                    f"(ID: <code>{user_id}</code>)\n"
                    f"📞 Telefon: {user['phone'] if user and user['phone'] else '—'}\n\n"
                    f"Buyurtma ID: {order_id}\n"
                    f"Yakunlash: /buyurtma_yakunla_{order_id}"
                )
            except Exception:
                pass
        return web.json_response({
            "success": True, "payment_type": "uzs", "product_name": product["name"],
            "price_uzs": product["price_uzs"], "loyalty_bonus": loyalty_bonus,
        })

    user = await db.get_user(user_id)
    if user["coins"] < product["cost"]:
        return web.json_response({"error": "not_enough_coins"}, status=400)

    new_balance = await db.add_coins(user_id, -product["cost"])
    await db.record_shop_purchase(user_id, product_id, product["cost"], payment_type="coin")
    loyalty_bonus = await apply_shop_loyalty_bonus(user_id)
    if loyalty_bonus:
        new_balance += loyalty_bonus

    return web.json_response({
        "success": True, "coins": new_balance, "product_name": product["name"],
        "loyalty_bonus": loyalty_bonus,
    })


async def api_my_items(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    purchases = await db.get_user_purchases(user_id)
    items = [
        {
            "id": p["item_id"],
            "name": p["item_name"],
            "emoji": p["item_emoji"],
            "type": p["item_type"],
            "purchased_at": p["purchased_at"]
        }
        for p in purchases
    ]
    return web.json_response({"items": items})


async def api_quiz_complete(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    is_premium = await db.check_premium_status(user_id)
    reward_per_correct = QUIZ_REWARD_PER_CORRECT_PREMIUM if is_premium else QUIZ_REWARD_PER_CORRECT

    correct = int(body.get("correct", 0))
    correct = max(0, min(correct, MAX_QUIZ_QUESTIONS))
    total_questions = int(body.get("total_questions", correct))
    total_questions = max(correct, min(total_questions, MAX_QUIZ_QUESTIONS))
    earned = correct * reward_per_correct

    new_balance = await db.add_coins(user_id, earned)
    await db.increment_quiz_stats(user_id, total_questions, correct, earned)

    return web.json_response({
        "success": True,
        "coins": new_balance,
        "earned": earned,
        "is_premium": is_premium
    })


# ---------- YANGI TESTLAR (QUIZ SETS) API ----------

async def api_quizzes(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    referral_count = await db.get_referral_count(user_id)
    quiz_sets = await db.get_active_quiz_sets()

    result = [
        {
            "id": qs["id"],
            "title": qs["title"],
            "question_count": qs["question_count"],
            "required_referrals": qs["required_referrals"],
            "reward_per_correct": qs["reward_per_correct"],
            "question_timer_seconds": qs["question_timer_seconds"],
            "unlocked": referral_count >= qs["required_referrals"],
        }
        for qs in quiz_sets
    ]
    return web.json_response({"quizzes": result, "referral_count": referral_count})


async def api_quiz_questions(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    quiz_id = int(request.match_info["quiz_id"])
    quiz_set = await db.get_quiz_set(quiz_id)
    if not quiz_set:
        return web.json_response({"error": "invalid_quiz"}, status=404)

    referral_count = await db.get_referral_count(user_id)
    if referral_count < quiz_set["required_referrals"]:
        return web.json_response({"error": "locked"}, status=403)

    is_premium = await db.check_premium_status(user_id)
    reward_per_correct = quiz_set["reward_per_correct"] + (QUIZ_PREMIUM_BONUS_PER_CORRECT if is_premium else 0)

    questions = await db.get_quiz_questions(quiz_id)
    result = [
        {
            "id": q["id"],
            "question": q["question_text"],
            "a": q["option_a"], "b": q["option_b"], "c": q["option_c"], "d": q["option_d"],
            "correct": q["correct_option"],
        }
        for q in questions
    ]

    return web.json_response({
        "quiz": {
            "id": quiz_set["id"],
            "title": quiz_set["title"],
            "reward_per_correct": reward_per_correct,
            "base_reward_per_correct": quiz_set["reward_per_correct"],
            "premium_reward_per_correct": quiz_set["reward_per_correct"] + QUIZ_PREMIUM_BONUS_PER_CORRECT,
            "is_premium": is_premium,
            "question_timer_seconds": quiz_set["question_timer_seconds"],
        },
        "questions": result,
    })


async def api_quiz_submit(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    quiz_id = body.get("quiz_id")
    quiz_set = await db.get_quiz_set(quiz_id)
    if not quiz_set:
        return web.json_response({"error": "invalid_quiz"}, status=400)

    referral_count = await db.get_referral_count(user_id)
    if referral_count < quiz_set["required_referrals"]:
        return web.json_response({"error": "locked"}, status=403)

    questions = await db.get_quiz_questions(quiz_id)
    total_questions = len(questions)

    is_premium = await db.check_premium_status(user_id)
    reward_per_correct = quiz_set["reward_per_correct"] + (QUIZ_PREMIUM_BONUS_PER_CORRECT if is_premium else 0)

    correct = int(body.get("correct", 0))
    correct = max(0, min(correct, total_questions))
    earned = correct * reward_per_correct

    new_balance = await db.add_coins(user_id, earned)
    await db.increment_quiz_stats(user_id, total_questions, correct, earned)
    xp_info = await award_xp_and_level_rewards(user_id, correct * QUIZ_XP_PER_CORRECT, notify=True)
    if xp_info.get("level_up_coins"):
        new_balance = await db.add_coins(user_id, 0)

    return web.json_response({
        "success": True, "earned": earned, "coins": new_balance,
        "level": xp_info["level"], "level_up": xp_info["level_up_coins"] > 0,
        "level_up_coins": xp_info.get("level_up_coins", 0),
        "new_badges": xp_info.get("new_badges", []),
    })


# ================== CHALLENGE API (Mini App) ==================

async def api_buy_stars_premium(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_body"}, status=400)

    months = int(body.get("months", 0))

    if months == 1:
        stars = PREMIUM_1MONTH_STARS_PRICE
        payload = f"premium_1_{user_id}"
        title = "1 oylik Telegram Premium"
        description = "To'lovdan so'ng admin sizga Premiumni qo'lda olib beradi."
    elif months in PREMIUM_STARS_PRICE:
        stars = PREMIUM_STARS_PRICE[months]
        payload = f"premium_{months}_{user_id}"
        title = f"{months} oylik Telegram Premium"
        description = f"To'lovdan so'ng Premium avtomatik ulanadi ({stars} Stars)."
    else:
        return web.json_response({"error": "invalid_months"}, status=400)

    try:
        link = await bot.create_invoice_link(
            title=title, description=description, payload=payload,
            provider_token="", currency="XTR",
            prices=[LabeledPrice(label=title, amount=stars)],
        )
    except Exception as e:
        logger.exception("create_invoice_link xatosi (premium)")
        return web.json_response({"error": "invoice_failed"}, status=500)

    return web.json_response({"invoice_link": link})


async def api_buy_stars_coins(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_body"}, status=400)

    stars = int(body.get("stars", 0))
    coins = int(body.get("coins", 0))

    # Faqat oldindan belgilangan paketlarga ruxsat (o'zboshimcha narx yubormasin)
    valid_packages = {100: 5000, 300: 16000, 500: 28000, 1000: 60000}
    if valid_packages.get(stars) != coins:
        return web.json_response({"error": "invalid_package"}, status=400)

    payload = f"coins_{coins}_{user_id}"
    title = f"{coins:,} koin"

    try:
        link = await bot.create_invoice_link(
            title=title, description=f"{stars} Stars evaziga {coins:,} koin hisobingizga qo'shiladi.",
            payload=payload, provider_token="", currency="XTR",
            prices=[LabeledPrice(label=title, amount=stars)],
        )
    except Exception as e:
        logger.exception("create_invoice_link xatosi (coins)")
        return web.json_response({"error": "invoice_failed"}, status=500)

    return web.json_response({"invoice_link": link})


async def api_challenge(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    challenge = await db.get_active_challenge()
    if not challenge:
        return web.json_response({"challenge": None})

    participant = await db.get_participant(challenge["id"], user_id)
    final_questions = await db.get_challenge_questions(challenge["id"], None)

    result = {
        "id": challenge["id"],
        "book_title": challenge["book_title"],
        "cover_url": f"/api/challenge_cover/{challenge['id']}" if challenge["cover_path"] else None,
        "description": challenge["description"],
        "days_count": challenge["days_count"],
        "pass_percent": challenge["pass_percent"],
        "joined": participant is not None,
        "join_cost": CHALLENGE_JOIN_COST,
        "final_questions_count": len(final_questions),
        "pass_bonus_coins": FINAL_PASS_BONUS_COINS,
        "pass_bonus_xp": FINAL_PASS_BONUS_XP,
    }
    if participant:
        result.update({
            "current_day": participant["current_day"],
            "streak": participant["streak"],
            "total_xp": participant["total_xp"],
            "total_coins": participant["total_coins"],
            "status": participant["status"],
            "final_percent": float(participant["final_percent"]) if participant["final_percent"] is not None else None,
        })
        if participant["current_day"] <= challenge["days_count"]:
            prev_day = participant["current_day"] - 1
            result["day_locked"] = False
            if prev_day >= 1:
                # Oldingi kun tugatilganmi — shunga qarab 24 soatlik qulf hisoblanadi
                progress = await db.get_daily_progress(participant["id"], prev_day)
                if progress:
                    completed_dt = datetime.strptime(progress["completed_at"], "%Y-%m-%d %H:%M")
                    unlock_at = completed_dt + timedelta(hours=CHALLENGE_DAY_UNLOCK_HOURS)
                    result["day_locked_until"] = unlock_at.strftime("%Y-%m-%d %H:%M")
                    result["day_locked"] = datetime.now() < unlock_at

    return web.json_response({"challenge": result})


async def api_challenge_join(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    challenge = await db.get_active_challenge()
    if not challenge:
        return web.json_response({"error": "no_active_challenge"}, status=404)

    existing_participant = await db.get_participant(challenge["id"], user_id)
    if existing_participant:
        # Allaqachon qo'shilgan — qayta pul yechilmasin
        return web.json_response({"success": True, "participant_id": existing_participant["id"]})

    user = await db.get_user(user_id)
    if not user or user["coins"] < CHALLENGE_JOIN_COST:
        return web.json_response({"error": "not_enough_coins", "required": CHALLENGE_JOIN_COST}, status=400)

    await db.add_coins(user_id, -CHALLENGE_JOIN_COST)
    participant = await db.join_challenge(challenge["id"], user_id)
    return web.json_response({"success": True, "participant_id": participant["id"], "coins_spent": CHALLENGE_JOIN_COST})


async def api_challenge_day(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    day_number = int(request.match_info["day_number"])

    challenge = await db.get_active_challenge()
    if not challenge:
        return web.json_response({"error": "no_active_challenge"}, status=404)

    participant = await db.get_participant(challenge["id"], user_id)
    if not participant:
        return web.json_response({"error": "not_joined"}, status=403)

    if day_number != participant["current_day"]:
        return web.json_response({"error": "wrong_day"}, status=400)

    prev_day = day_number - 1
    if prev_day >= 1:
        progress = await db.get_daily_progress(participant["id"], prev_day)
        if progress:
            completed_dt = datetime.strptime(progress["completed_at"], "%Y-%m-%d %H:%M")
            unlock_at = completed_dt + timedelta(hours=CHALLENGE_DAY_UNLOCK_HOURS)
            if datetime.now() < unlock_at:
                return web.json_response({
                    "error": "day_locked",
                    "unlock_at": unlock_at.strftime("%Y-%m-%d %H:%M"),
                }, status=403)

    day = await db.get_challenge_day(challenge["id"], day_number)
    questions = await db.get_challenge_questions(challenge["id"], day_number)

    return web.json_response({
        "day_number": day_number,
        "reading_text": day["reading_text"] if day else "",
        "question_timer_seconds": CHALLENGE_QUESTION_TIMER,
        "questions": [
            {"id": q["id"], "question": q["question"], "a": q["option_a"], "b": q["option_b"],
             "c": q["option_c"], "d": q["option_d"]}
            for q in questions
        ],
    })


async def api_challenge_day_submit(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    day_number = int(body.get("day_number", 0))
    answers = body.get("answers", {})  # {question_id: "A"/"B"/"C"/"D"}

    challenge = await db.get_active_challenge()
    if not challenge:
        return web.json_response({"error": "no_active_challenge"}, status=404)

    participant = await db.get_participant(challenge["id"], user_id)
    if not participant or day_number != participant["current_day"]:
        return web.json_response({"error": "invalid_state"}, status=400)

    questions = await db.get_challenge_questions(challenge["id"], day_number)
    total = len(questions)
    correct = 0
    for q in questions:
        given = str(answers.get(str(q["id"]), "")).strip().upper()
        if given and given == (q["correct_option"] or "").strip().upper():
            correct += 1

    coins_earned = correct * CHALLENGE_COIN_PER_CORRECT
    xp_earned = correct * CHALLENGE_XP_PER_CORRECT
    new_streak = participant["streak"] + 1

    await db.record_daily_progress(participant["id"], day_number, correct, total, coins_earned, xp_earned, new_streak)
    new_balance = await db.add_coins(user_id, coins_earned)
    xp_info = await award_xp_and_level_rewards(user_id, xp_earned, notify=False)
    if xp_info.get("level_up_coins"):
        new_balance = await db.add_coins(user_id, 0)  # eng so'nggi balansni olish uchun

    final_unlocked = (day_number + 1) > challenge["days_count"]

    return web.json_response({
        "success": True, "correct": correct, "total": total,
        "coins_earned": coins_earned, "xp_earned": xp_earned,
        "streak": new_streak, "coins": new_balance,
        "level": xp_info["level"], "final_unlocked": final_unlocked,
        "level_up": xp_info["level_up_coins"] > 0,
        "level_up_coins": xp_info.get("level_up_coins", 0),
        "new_badges": xp_info.get("new_badges", []),
    })


async def api_challenge_final(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    challenge = await db.get_active_challenge()
    if not challenge:
        return web.json_response({"error": "no_active_challenge"}, status=404)

    participant = await db.get_participant(challenge["id"], user_id)
    if not participant or participant["current_day"] <= challenge["days_count"]:
        return web.json_response({"error": "not_ready"}, status=403)
    if participant["status"] != "in_progress":
        return web.json_response({"error": "already_completed"}, status=400)

    questions = await db.get_challenge_questions(challenge["id"], None)
    return web.json_response({
        "question_timer_seconds": FINAL_TEST_QUESTION_TIMER,
        "pass_percent": challenge["pass_percent"],
        "questions": [
            {"id": q["id"], "question": q["question"], "a": q["option_a"], "b": q["option_b"],
             "c": q["option_c"], "d": q["option_d"]}
            for q in questions
        ],
    })


async def api_challenge_final_submit(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    answers = body.get("answers", {})  # {question_id: "A"/"B"/"C"/"D"}

    challenge = await db.get_active_challenge()
    if not challenge:
        return web.json_response({"error": "no_active_challenge"}, status=404)

    participant = await db.get_participant(challenge["id"], user_id)
    if not participant or participant["status"] != "in_progress":
        return web.json_response({"error": "invalid_state"}, status=400)

    questions = await db.get_challenge_questions(challenge["id"], None)
    total = len(questions)
    correct = 0
    for q in questions:
        given = str(answers.get(str(q["id"]), "")).strip().upper()
        if given and given == (q["correct_option"] or "").strip().upper():
            correct += 1
    percent = round((correct / total) * 100, 1) if total else 0

    coins_earned = correct * FINAL_TEST_COIN_PER_CORRECT
    xp_earned = correct * FINAL_TEST_XP_PER_CORRECT
    passed = percent >= challenge["pass_percent"]
    status = "completed" if passed else "failed"

    # O'tish balidan (masalan 70%) ko'p to'plaganlarga BIR MARTALIK qo'shimcha bonus
    pass_bonus_coins = 0
    pass_bonus_xp = 0
    if passed:
        pass_bonus_coins = FINAL_PASS_BONUS_COINS
        pass_bonus_xp = FINAL_PASS_BONUS_XP
        coins_earned += pass_bonus_coins
        xp_earned += pass_bonus_xp

    await db.record_final_result(participant["id"], correct, total, percent, coins_earned, xp_earned, status)
    new_balance = await db.add_coins(user_id, coins_earned)
    xp_info = await award_xp_and_level_rewards(user_id, xp_earned, notify=False)
    if xp_info.get("level_up_coins"):
        new_balance = await db.add_coins(user_id, 0)

    certificate_code = None
    if passed:
        certificate_code = await db.create_certificate(user_id, challenge["id"], challenge["book_title"], percent)
        await db.add_badge(user_id, challenge["id"], f"{challenge['book_title']} — Challenge Master")

    return web.json_response({
        "success": True, "correct": correct, "total": total, "percent": percent,
        "passed": passed, "coins_earned": coins_earned, "xp_earned": xp_earned,
        "pass_bonus_coins": pass_bonus_coins, "pass_bonus_xp": pass_bonus_xp,
        "coins": new_balance, "level": xp_info["level"],
        "certificate_code": certificate_code,
        "level_up": xp_info["level_up_coins"] > 0,
        "level_up_coins": xp_info.get("level_up_coins", 0),
        "new_badges": xp_info.get("new_badges", []),
    })


async def api_challenge_leaderboard(request: web.Request):
    challenge_id = request.query.get("challenge_id")
    if challenge_id:
        challenge = await db.get_challenge(int(challenge_id))
    else:
        challenge = await db.get_active_challenge()
    if not challenge:
        return web.json_response({"leaderboard": []})

    rows = await db.get_challenge_leaderboard(challenge["id"], limit=50)
    return web.json_response({
        "leaderboard": [
            {
                "rank": i + 1, "user_id": r["user_id"], "username": r["username"],
                "percent": float(r["final_percent"]), "xp": r["total_xp"],
                "avatar_url": f"/api/avatar/{r['user_id']}",
            }
            for i, r in enumerate(rows)
        ],
    })


async def api_verify_certificate(request: web.Request):
    code = request.query.get("code", "").strip()
    if not code:
        return web.json_response({"error": "missing_code"}, status=400)

    cert = await db.get_certificate_by_code(code)
    if not cert:
        return web.json_response({"valid": False})

    return web.json_response({
        "valid": True,
        "code": cert["code"],
        "username": cert["username"],
        "book_title": cert["book_title"],
        "percent": float(cert["percent"]),
        "issued_at": cert["issued_at"],
    })


async def api_challenge_cover(request: web.Request):
    challenge_id = int(request.match_info["challenge_id"])
    challenge = await db.get_challenge(challenge_id)
    if not challenge or not challenge["cover_path"]:
        return web.Response(status=404)
    path = os.path.join(BOOK_STORAGE_DIR, challenge["cover_path"])
    if not os.path.isfile(path):
        return web.Response(status=404)
    return web.FileResponse(path)


# ---------- OCHIQ FOYDALANUVCHI PROFILI (Reyting -> Profil) API ----------

async def api_user_profile(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    target_user_id = int(request.match_info["user_id"])
    profile = await db.get_user_public_profile(target_user_id)
    if not profile:
        return web.json_response({"error": "not_found"}, status=404)

    profile["avatar_url"] = f"/api/avatar/{target_user_id}" if profile.get("avatar_path") else None
    profile.pop("avatar_path", None)

    return web.json_response(profile)


async def api_leaderboard(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    coins_rows = await db.get_coins_leaderboard(10)
    referral_rows = await db.get_referral_leaderboard(10)
    quiz_rows = await db.get_quiz_leaderboard(10)

    def display_name(username, user_id):
        if username and not str(username).isdigit():
            return f"@{username}"
        return f"ID{str(user_id)[-4:]}"

    def row_is_premium(r):
        if not r["is_premium"]:
            return False
        expires = r["premium_expires_at"]
        return expires is None or expires > datetime.now()

    def level_badge_icon(level):
        if level >= 10:
            return "🥇"
        if level >= 6:
            return "🥈"
        if level >= 3:
            return "🥉"
        return None

    coins_list = [
        {
            "name": display_name(r["username"], r["user_id"]), "value": r["coins"],
            "user_id": r["user_id"],
            "avatar_url": f"/api/avatar/{r['user_id']}" if r["avatar_path"] else None,
            "is_premium": row_is_premium(r),
            "level": r["level"],
            "level_badge": level_badge_icon(r["level"]),
        }
        for r in coins_rows
    ]
    referral_list = [
        {
            "name": display_name(r["username"], r["user_id"]), "value": r["cnt"],
            "user_id": r["user_id"],
            "avatar_url": f"/api/avatar/{r['user_id']}" if r["avatar_path"] else None,
            "is_premium": row_is_premium(r),
        }
        for r in referral_rows
    ]
    quiz_list = [
        {
            "name": display_name(r["username"], r["user_id"]), "value": r["cnt"],
            "user_id": r["user_id"],
            "avatar_url": f"/api/avatar/{r['user_id']}" if r["avatar_path"] else None,
            "is_premium": row_is_premium(r),
        }
        for r in quiz_rows
    ]

    return web.json_response({"coins": coins_list, "referrals": referral_list, "quiz": quiz_list})


# ---------- SIRLI SANDIQ API ----------

async def api_chests(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    user = await db.get_user(user_id)
    if not user:
        return web.json_response({"error": "user not found"}, status=404)

    chests = await db.get_active_chests()
    result = []
    for chest in chests:
        key = chest["id"]
        opened = await db.get_chest_opens_today(user_id, key)
        result.append({
            "id": key,
            "name": chest["name"],
            "emoji": chest["emoji"],
            "cost": chest["cost"],
            "daily_limit": chest["daily_limit"],
            "opened_today": opened,
            "remaining": max(0, chest["daily_limit"] - opened),
            "can_open": opened < chest["daily_limit"] and user["coins"] >= chest["cost"],
            "rewards": chest["rewards"],  # shaffoflik: ochishdan oldin mukofotlar ko'rinadi
        })

    return web.json_response({"chests": result, "coins": user["coins"]})


# ---------- MUROJAAT VA TAKLIFLAR ----------

async def api_send_feedback(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    username = user_data.get("username") or user_data.get("first_name", "User")

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "empty"}, status=400)
    text = text[:2000]

    safe_text = html.escape(text)
    uname_display = f"@{username}" if user_data.get("username") else username
    message_text = (
        f"📩 <b>Yangi murojaat/taklif</b>\n\n"
        f"👤 {html.escape(str(uname_display))} (ID: <code>{user_id}</code>)\n\n"
        f"💬 {safe_text}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, message_text)
        except Exception as e:
            logger.warning(f"Fikr-mulohaza yuborishda xato (admin {admin_id}): {e}")

    return web.json_response({"success": True})


async def api_open_chest(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    chest_id = body.get("chest_id")
    chest = await db.get_chest_by_id(chest_id)
    if not chest:
        return web.json_response({"error": "invalid chest"}, status=400)

    user = await db.get_user(user_id)
    if not user:
        return web.json_response({"error": "user not found"}, status=404)

    opened = await db.get_chest_opens_today(user_id, chest_id)
    if opened >= chest["daily_limit"]:
        return web.json_response({"error": "limit_reached"}, status=400)
    if user["coins"] < chest["cost"]:
        return web.json_response({"error": "not_enough_coins"}, status=400)

    new_balance = await db.add_coins(user_id, -chest["cost"])

    rand = random.randint(1, 100)
    cumulative = 0
    chosen = chest["rewards"][0]
    for reward in chest["rewards"]:
        cumulative += reward["chance"]
        if rand <= cumulative:
            chosen = reward
            break

    if chosen["type"] == "coins":
        amount = random.randint(chosen["min"], chosen["max"])
        new_balance = await db.add_coins(user_id, amount)
        await db.record_chest_open(user_id, chest_id, "coins", amount)
        reward_text = f"+{amount} koin"
        reward_type = "coins"
        reward_amount = amount
    elif chosen["type"] == "premium":
        days = chosen["days"]
        await db.activate_premium(user_id, days=days)
        await db.record_chest_open(user_id, chest_id, "premium", days)
        reward_text = f"+{days} kun Premium"
        reward_type = "premium"
        reward_amount = days
    elif chosen["type"] == "badge":
        badge_name = "🎁 Sirli sandiq nishoni"
        await db.add_badge(user_id, None, badge_name)
        await db.record_chest_open(user_id, chest_id, "badge", 1)
        reward_text = f"🏅 Nishon: {badge_name}"
        reward_type = "badge"
        reward_amount = 1
    elif chosen["type"] == "streak_freeze":
        new_freezes = await db.add_streak_freeze(user_id, 1)
        await db.record_chest_open(user_id, chest_id, "streak_freeze", 1)
        reward_text = "❄️ +1 Streak-freeze"
        reward_type = "streak_freeze"
        reward_amount = new_freezes
    else:  # nothing
        await db.record_chest_open(user_id, chest_id, "nothing", 0)
        reward_text = "😐 Afsuski, bu safar hech narsa chiqmadi..."
        reward_type = "nothing"
        reward_amount = 0

    return web.json_response({
        "success": True,
        "reward_type": reward_type,
        "reward_amount": reward_amount,
        "reward_text": reward_text,
        "new_balance": new_balance,
        "chest_name": chest["name"],
        "chest_emoji": chest["emoji"]
    })


# ---------- BOSH SAHIFA API ----------

async def api_check_force_sub(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    not_subscribed = await check_subscription(user_id)
    channels = [
        {"username": ch.lstrip("@"), "url": f"https://t.me/{ch.lstrip('@')}"}
        for ch in not_subscribed
    ]
    return web.json_response({"ok": len(channels) == 0, "channels": channels})


async def api_mark_onboarding_seen(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)
    await db.mark_onboarding_seen(user_data["id"])
    return web.json_response({"success": True})


async def api_home(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    username = user_data.get("username") or user_data.get("first_name", "User")
    await db.create_user_if_missing(user_id, username)

    client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote
    if client_ip:
        await db.set_signup_ip_if_missing(user_id, client_ip)
    await db.record_active_hour(user_id, datetime.now().hour)
    user = await db.get_user(user_id)
    is_premium = await db.check_premium_status(user_id)
    referral_count = await db.get_referral_count(user_id)
    referral_rank = await db.get_referral_rank(user_id)
    free_bonus_claimed = await db.has_claimed_free_daily_bonus(user_id)

    tasks = await db.get_tasks_with_progress(user_id)
    unclaimed_tasks = sum(1 for t in tasks if t["completed"] and not t["claimed"])

    draw = await db.get_current_weekly_draw()
    weekly_info = None
    if draw:
        weekly_progress = await db.get_weekly_referral_progress(user_id, draw["week_start"])
        weekly_info = {
            "goal": draw["goal"],
            "reward_text": draw["reward_text"],
            "progress": min(weekly_progress, draw["goal"]) if draw["goal"] > 0 else 0,
            "week_start": draw["week_start"],
            "week_end": draw["week_end"],
            "required_channels": [c for c in (draw["required_channels"] or "").split(",") if c.strip()],
            "prizes": [p for p in (draw["prizes"] or "").split("\n") if p.strip()],
            "image_url": f"/api/draw_image/{draw['id']}" if draw["image_path"] else None,
        }

    winners = await db.get_recent_winners(5)
    winners_list = [
        {
            "username": w["username"] or f"ID{str(w['user_id'])[-4:]}",
            "week_label": w["week_label"],
            "referral_count": w["referral_count"],
            "prize": w["prize"],
            "won_date": w["won_date"],
        }
        for w in winners
    ]

    return web.json_response({
        "name": (user["username"] if user and user["username"] else username),
        "coins": user["coins"],
        "referral_count": referral_count,
        "referral_rank": referral_rank,
        "streak_days": user["streak_days"] or 0,
        "streak_freezes_left": user["streak_freezes"] if user["streak_freezes"] is not None else 2,
        "has_seen_onboarding": user["has_seen_onboarding"] or False,
        "is_premium": is_premium,
        "free_daily_bonus_claimed": free_bonus_claimed,
        "free_daily_bonus_amount": FREE_DAILY_BONUS_AMOUNT,
        "unclaimed_tasks": unclaimed_tasks,
        "weekly_draw": weekly_info,
        "recent_winners": winners_list,
    })


async def api_claim_daily_bonus(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    amount = await db.claim_free_daily_bonus(user_id, FREE_DAILY_BONUS_AMOUNT)
    if amount == 0:
        return web.json_response({"error": "already_claimed"}, status=400)

    user = await db.get_user(user_id)
    return web.json_response({"success": True, "earned": amount, "coins": user["coins"]})


# ---------- VAZIFALAR API ----------

async def api_tasks(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    tasks = await db.get_tasks_with_progress(user_id)
    return web.json_response({"tasks": tasks})


async def api_claim_task(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    task_code = body.get("task_code")
    success, result, new_balance = await db.claim_task(user_id, task_code)
    if not success:
        return web.json_response({"error": result}, status=400)

    return web.json_response({"success": True, "earned": result, "coins": new_balance})


# ---------- MUTOLAA TARIXI API ----------

async def api_reading_history(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    rows = await db.get_reading_history(user_id, 20)
    history = [
        {
            "book_title": r["book_title"] or "Kitobzor",
            "page_number": r["page_number"],
            "coins_earned": r["coins_earned"],
            "read_at": r["read_at"],
        }
        for r in rows
    ]
    return web.json_response({"history": history})


# ---------- HAFTALIK TANLOV API ----------

async def api_weekly_draw(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    draw = await db.get_current_weekly_draw()
    if not draw:
        return web.json_response({"draw": None})

    progress = await db.get_weekly_referral_progress(user_id, draw["week_start"])
    return web.json_response({
        "draw": {
            "goal": draw["goal"],
            "reward_text": draw["reward_text"],
            "progress": min(progress, draw["goal"]),
            "week_start": draw["week_start"],
            "week_end": draw["week_end"],
            "required_channels": draw["required_channels"] or "",
            "prizes": draw["prizes"] or "",
            "image_url": f"/api/draw_image/{draw['id']}" if draw["image_path"] else None,
        }
    })


# ---------- KITOBLAR (PDF KUTUBXONA) API ----------

async def api_books(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    await db.create_user_if_missing(user_id, user_data.get("username") or user_data.get("first_name", "User"))

    books = await db.get_all_books()
    unlocked_ids = await db.get_user_unlocked_book_ids(user_id)
    referral_count = await db.get_referral_count(user_id)
    user = await db.get_user(user_id)
    is_premium = await db.check_premium_status(user_id)

    result = []
    for b in books:
        unlocked = b["id"] in unlocked_ids
        book_type = b["book_type"] or "pdf"
        required_coins = b["required_coins"]
        if is_premium and required_coins > 0:
            required_coins = int(round(required_coins * (1 - BOOK_COIN_DISCOUNT_PREMIUM)))

        entry = {
            "id": b["id"],
            "title": b["title"],
            "author": b["author"],
            "genre": b["genre"],
            "cover_url": f"/api/cover/{b['id']}" if b["cover_path"] else None,
            "book_type": book_type,
            "page_count": b["page_count"],
            "required_referrals": b["required_referrals"],
            "required_coins": required_coins,
            "original_required_coins": b["required_coins"],
            "premium_only": b["premium_only"],
            "unlocked": unlocked,
            "current_page": 0,
        }

        if book_type == "audio":
            chapter_count = b["chapter_count"] or 0
            current_chapter = 1
            audio_progress_seconds = 0
            if unlocked:
                ap = await db.get_audio_progress(user_id, b["id"])
                if ap:
                    current_chapter = ap["chapter_number"]
                    audio_progress_seconds = ap["position_seconds"]
            entry["chapter_count"] = chapter_count
            entry["current_chapter"] = current_chapter
            entry["audio_progress_seconds"] = audio_progress_seconds
        else:
            progress = await db.get_book_progress(user_id, b["id"]) if unlocked else 0
            entry["current_page"] = progress

        result.append(entry)

    return web.json_response({
        "books": result,
        "referral_count": referral_count,
        "coins": user["coins"],
        "is_premium": is_premium,
    })


async def api_book_cover(request: web.Request):
    book_id = int(request.match_info["book_id"])
    book = await db.get_book(book_id)
    if not book or not book["cover_path"]:
        return web.Response(status=404)
    full_path = os.path.join(BOOK_STORAGE_DIR, book["cover_path"])
    if not os.path.exists(full_path):
        return web.Response(status=404)
    return web.FileResponse(full_path)


async def api_draw_image(request: web.Request):
    draw_id = int(request.match_info["draw_id"])
    draw = await db.get_draw_by_id(draw_id)
    if not draw or not draw["image_path"]:
        return web.Response(status=404)
    full_path = os.path.join(BOOK_STORAGE_DIR, draw["image_path"])
    if not os.path.exists(full_path):
        return web.Response(status=404)
    return web.FileResponse(full_path)


async def api_avatar(request: web.Request):
    user_id = int(request.match_info["user_id"])
    user = await db.get_user(user_id)
    if not user or not user["avatar_path"]:
        return web.Response(status=404)
    full_path = os.path.join(BOOK_STORAGE_DIR, user["avatar_path"])
    if not os.path.exists(full_path):
        return web.Response(status=404)
    return web.FileResponse(full_path)


async def api_book_page_data(request: web.Request):
    """Sahifa turini (matn/rasm) va mazmunini JSON qilib qaytaradi."""
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    book_id = int(request.match_info["book_id"])
    page_number = int(request.match_info["page_number"])

    unlocked = await db.is_book_unlocked(user_id, book_id)
    if not unlocked:
        return web.json_response({"error": "locked"}, status=403)

    page = await db.get_book_page(book_id, page_number)
    if not page:
        return web.json_response({"error": "not_found"}, status=404)

    is_premium = await db.check_premium_status(user_id)
    read_count = await db.get_read_pages_count(user_id, book_id)
    progress = {
        "pages_read_in_book": read_count,
        "milestone_pages": MILESTONE_PAGES,
        "milestone_bonus": MILESTONE_BONUS_PREMIUM if is_premium else MILESTONE_BONUS,
        "pages_to_next_milestone": MILESTONE_PAGES - (read_count % MILESTONE_PAGES),
    }

    page_type = page["page_type"] if page["page_type"] else "image"
    if page_type == "text":
        return web.json_response({"type": "text", "content": page["text_content"] or "", **progress})
    return web.json_response({
        "type": "image",
        "image_url": f"/api/page_image/{book_id}/{page_number}?initData={quote(init_data)}",
        **progress,
    })


async def api_book_page_image(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.Response(status=401)

    user_id = user_data["id"]
    book_id = int(request.match_info["book_id"])
    page_number = int(request.match_info["page_number"])

    unlocked = await db.is_book_unlocked(user_id, book_id)
    if not unlocked:
        return web.Response(status=403)

    page = await db.get_book_page(book_id, page_number)
    if not page:
        return web.Response(status=404)
    full_path = os.path.join(BOOK_STORAGE_DIR, page["image_path"])
    if not os.path.exists(full_path):
        return web.Response(status=404)
    return web.FileResponse(full_path)


async def api_book_audio_chapters(request: web.Request):
    """Audio kitobning bob ro'yxatini (nomi, davomiyligi) qaytaradi."""
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    book_id = int(request.match_info["book_id"])

    unlocked = await db.is_book_unlocked(user_id, book_id)
    if not unlocked:
        return web.json_response({"error": "locked"}, status=403)

    book = await db.get_book(book_id)
    if not book or (book["book_type"] or "pdf") != "audio":
        return web.json_response({"error": "not_found"}, status=404)

    chapters = await db.get_book_audio_chapters(book_id)
    progress = await db.get_audio_progress(user_id, book_id)

    return web.json_response({
        "book_id": book_id,
        "title": book["title"],
        "chapters": [
            {
                "chapter_number": c["chapter_number"],
                "title": c["title"],
                "duration_seconds": c["duration_seconds"],
                "audio_url": f"/api/book_audio_file/{book_id}/{c['chapter_number']}?initData={quote(init_data)}",
            }
            for c in chapters
        ],
        "current_chapter": progress["chapter_number"] if progress else 1,
        "position_seconds": progress["position_seconds"] if progress else 0,
    })


async def api_book_audio_file(request: web.Request):
    """Audio bob faylini oqim (stream) sifatida uzatadi — aiohttp Range so'rovlarini
    o'zi qo'llab-quvvatlaydi, shuning uchun pleyerda oldinga/orqaga o'tish ishlaydi."""
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.Response(status=401)

    user_id = user_data["id"]
    book_id = int(request.match_info["book_id"])
    chapter_number = int(request.match_info["chapter_number"])

    unlocked = await db.is_book_unlocked(user_id, book_id)
    if not unlocked:
        return web.Response(status=403)

    chapter = await db.get_book_audio_chapter(book_id, chapter_number)
    if not chapter:
        return web.Response(status=404)
    full_path = os.path.join(BOOK_STORAGE_DIR, chapter["audio_path"])
    if not os.path.exists(full_path):
        return web.Response(status=404)
    return web.FileResponse(full_path)


async def api_book_audio_progress(request: web.Request):
    """Audio pozitsiyani saqlaydi; bob birinchi marta tugatilganda mukofot beradi."""
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    book_id = body.get("book_id")
    chapter_number = body.get("chapter_number")
    position_seconds = body.get("position_seconds", 0)
    finished = bool(body.get("finished", False))

    book = await db.get_book(book_id)
    if not book or (book["book_type"] or "pdf") != "audio":
        return web.json_response({"error": "invalid_book"}, status=400)

    unlocked = await db.is_book_unlocked(user_id, book_id)
    if not unlocked:
        return web.json_response({"error": "locked"}, status=403)

    if not isinstance(chapter_number, int) or chapter_number < 1 or chapter_number > (book["chapter_count"] or 0):
        return web.json_response({"error": "invalid_chapter"}, status=400)

    try:
        position_seconds = max(0, int(position_seconds))
    except (TypeError, ValueError):
        position_seconds = 0

    await db.set_audio_progress(user_id, book_id, chapter_number, position_seconds)

    earned = 0
    xp_info = None
    already_read = await db.has_read_audio_chapter(user_id, book_id, chapter_number)
    if finished and not already_read:
        await db.mark_audio_chapter_read(user_id, book_id, chapter_number)
        is_premium = await db.check_premium_status(user_id)
        earned = AUDIO_CHAPTER_COIN_BONUS_PREMIUM if is_premium else AUDIO_CHAPTER_COIN_BONUS
        await db.add_coins(user_id, earned)
        xp_info = await award_xp_and_level_rewards(user_id, AUDIO_CHAPTER_XP_BONUS, notify=True)

    user = await db.get_user(user_id)
    return web.json_response({
        "success": True,
        "earned": earned,
        "coins": user["coins"],
        "already_read": already_read,
        "level_up": bool(xp_info and xp_info.get("level_up_coins")),
        "level_up_coins": xp_info.get("level_up_coins", 0) if xp_info else 0,
        "new_badges": xp_info.get("new_badges", []) if xp_info else [],
        "level": xp_info["level"] if xp_info else None,
    })


async def api_unlock_book(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    book_id = body.get("book_id")
    book = await db.get_book(book_id)
    if not book or not book["active"]:
        return web.json_response({"error": "invalid_book"}, status=400)

    already = await db.is_book_unlocked(user_id, book_id)
    if already:
        user = await db.get_user(user_id)
        return web.json_response({"success": True, "already": True, "coins": user["coins"]})

    referral_count = await db.get_referral_count(user_id)
    user = await db.get_user(user_id)
    is_premium = await db.check_premium_status(user_id)

    if book["premium_only"] and not is_premium:
        return web.json_response({"error": "premium_required"}, status=400)

    if referral_count < book["required_referrals"]:
        return web.json_response({
            "error": "not_enough_referrals",
            "required_referrals": book["required_referrals"],
            "current_referrals": referral_count,
        }, status=400)

    required_coins = book["required_coins"]
    if is_premium and required_coins > 0:
        required_coins = int(round(required_coins * (1 - BOOK_COIN_DISCOUNT_PREMIUM)))

    if user["coins"] < required_coins:
        return web.json_response({
            "error": "not_enough_coins",
            "required_coins": required_coins,
            "current_coins": user["coins"],
        }, status=400)

    new_balance = user["coins"]
    if required_coins > 0:
        new_balance = await db.add_coins(user_id, -required_coins)

    await db.unlock_book(user_id, book_id)

    return web.json_response({"success": True, "coins": new_balance})


async def api_book_earn(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    book_id = body.get("book_id")
    page_number = body.get("page_number")

    book = await db.get_book(book_id)
    if not book:
        return web.json_response({"error": "invalid_book"}, status=400)

    unlocked = await db.is_book_unlocked(user_id, book_id)
    if not unlocked:
        return web.json_response({"error": "locked"}, status=403)

    if not isinstance(page_number, int) or page_number < 1 or page_number > book["page_count"]:
        return web.json_response({"error": "invalid_page"}, status=400)

    already_read = await db.has_read_page(user_id, book_id, page_number)
    is_premium = await db.check_premium_status(user_id)
    bonus_amount = MILESTONE_BONUS_PREMIUM if is_premium else MILESTONE_BONUS

    earned = 0
    xp_info = None
    if not already_read:
        await db.mark_page_read(user_id, book_id, page_number)
        await db.increment_pages_read(user_id)

        new_milestones = await db.claim_reading_milestones(user_id, book_id, MILESTONE_PAGES)
        if new_milestones > 0:
            earned = new_milestones * bonus_amount
            await db.add_coins(user_id, earned)
            await db.record_reading_event(user_id, book["title"], page_number, earned)
            xp_info = await award_xp_and_level_rewards(
                user_id, new_milestones * XP_PER_25_PAGES, notify=True
            )

    await db.set_book_progress(user_id, book_id, page_number)
    user = await db.get_user(user_id)
    read_count = await db.get_read_pages_count(user_id, book_id)

    return web.json_response({
        "success": True,
        "earned": earned,
        "coins": user["coins"],
        "already_read": already_read,
        "pages_read_in_book": read_count,
        "milestone_pages": MILESTONE_PAGES,
        "milestone_bonus": bonus_amount,
        "pages_to_next_milestone": MILESTONE_PAGES - (read_count % MILESTONE_PAGES),
        "level_up": bool(xp_info and xp_info.get("level_up_coins")),
        "level_up_coins": xp_info.get("level_up_coins", 0) if xp_info else 0,
        "new_badges": xp_info.get("new_badges", []) if xp_info else [],
        "level": xp_info["level"] if xp_info else None,
    })


# ---------- KUNLIK O'QISH VAQTI (real, faol o'qish paytida) ----------

async def api_reading_progress(request: web.Request):
    """Kunlik o'qish progressini qaytaradi (ping yubormasdan, sahifa ochilganda chaqiriladi)."""
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)
    progress = await db.get_reading_progress_today(user_data["id"], DAILY_READING_TARGET_SECONDS)
    return web.json_response(progress)


async def api_reading_ping(request: web.Request):
    """Foydalanuvchi kitob o'qish ekranida, ilova ekranda faol turgan va oxirgi
    bir necha daqiqada sahifa varaqlagan bo'lsagina frontend shu endpoint'ga
    ping yuboradi. Har ping uchun server QAT'IY belgilangan miqdorni (20s)
    qo'shadi — mijoz yuborgan qiymatga ishonilmaydi, shu bilan soxta/fon
    hisoblashning oldi olinadi."""
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    result = await db.add_reading_seconds(user_id, DAILY_READING_PING_SECONDS, DAILY_READING_TARGET_SECONDS)

    new_balance = None
    xp_info = None
    if result["newly_claimed"]:
        new_balance = await db.add_coins(user_id, DAILY_READING_BONUS_COINS)
        xp_info = await award_xp_and_level_rewards(user_id, DAILY_READING_BONUS_XP, notify=False)

    return web.json_response({
        **result,
        "coins": new_balance,
        "bonus_coins": DAILY_READING_BONUS_COINS if result["newly_claimed"] else 0,
        "bonus_xp": DAILY_READING_BONUS_XP if result["newly_claimed"] else 0,
        "level_up": bool(xp_info and xp_info.get("level_up_coins")),
        "level_up_coins": xp_info.get("level_up_coins", 0) if xp_info else 0,
        "new_badges": xp_info.get("new_badges", []) if xp_info else [],
    })


# ---------- PROFIL KENGAYTMASI API ----------

async def api_profile_extra(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    user = await db.get_user(user_id)
    if not user:
        return web.json_response({"error": "user not found"}, status=404)

    referral_rank = await db.get_referral_rank(user_id)
    coins_rank = await db.get_coins_rank(user_id)
    quiz_rank = await db.get_quiz_rank(user_id)
    completed_books = await db.get_completed_books_count(user_id)
    is_premium = await db.check_premium_status(user_id)

    purchases = await db.get_user_purchases(user_id)
    badges = [
        {"name": p["item_name"], "emoji": p["item_emoji"]}
        for p in purchases if p["item_type"] == "badge"
    ]

    earned_badges_raw = await db.get_user_badges(user_id)
    earned_badges = [
        {"name": b["badge_name"], "issued_at": b["issued_at"]}
        for b in earned_badges_raw
    ]

    level = user["level"] or 1
    xp = user["xp"] or 0
    level_idx = min(level, len(db.LEVEL_XP_TABLE)) - 1
    current_threshold = db.LEVEL_XP_TABLE[level_idx]
    next_threshold = db.LEVEL_XP_TABLE[level_idx + 1] if level < len(db.LEVEL_XP_TABLE) else None

    premium_days_left = None
    if is_premium and user["premium_expires_at"]:
        remaining = user["premium_expires_at"] - datetime.now()
        premium_days_left = max(0, remaining.days + (1 if remaining.seconds > 0 else 0))

    return web.json_response({
        "referral_rank": referral_rank,
        "coins_rank": coins_rank,
        "quiz_rank": quiz_rank,
        "total_quiz_attempts": user["total_quiz_attempts"] or 0,
        "total_quiz_questions": user["total_quiz_questions"] or 0,
        "quiz_correct_total": user["quiz_correct_total"] or 0,
        "quiz_score_total": user["quiz_score_total"] or 0,
        "completed_books": completed_books,
        "region": user["region"],
        "phone": user["phone"],
        "joined_date": user["joined_date"],
        "is_premium": is_premium,
        "premium_days_left": premium_days_left,
        "badges": badges,
        "earned_badges": earned_badges,
        "level": level,
        "xp": xp,
        "xp_current_level_threshold": current_threshold,
        "xp_next_level_threshold": next_threshold,
    })


async def api_save_region(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    region = (body.get("region") or "").strip()
    if not region:
        return web.json_response({"error": "invalid_region"}, status=400)

    await db.set_user_region(user_id, region)
    return web.json_response({"success": True, "region": region})


async def index_page(request: web.Request):
    response = web.FileResponse("./index.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@web.middleware
async def error_middleware(request: web.Request, handler):
    """Har qanday kutilmagan xatolik (bug, DB muammosi va h.k.) foydalanuvchiga
    HTML "crash" sahifasi o'rniga toza JSON xato qaytarsin — aks holda frontend
    buni "aloqa uzildi" deb noto'g'ri talqin qiladi va sabab ko'rinmay qoladi."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception(f"Kutilmagan server xatosi: {request.path}")
        return web.json_response({"error": "server_error"}, status=500)


def create_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app.router.add_get("/", index_page)
    app.router.add_static("/assets/", path="./assets", name="assets")
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/shop_items", api_shop_items)
    app.router.add_post("/api/purchase", api_purchase)
    app.router.add_get("/api/premium_info", api_premium_info)
    app.router.add_post("/api/buy_premium_tier", api_buy_premium_tier)
    app.router.add_get("/api/my_items", api_my_items)
    app.router.add_post("/api/quiz_complete", api_quiz_complete)
    app.router.add_get("/api/leaderboard", api_leaderboard)
    app.router.add_get("/api/chests", api_chests)
    app.router.add_post("/api/open_chest", api_open_chest)
    app.router.add_post("/api/send_feedback", api_send_feedback)
    app.router.add_get("/api/reading_progress", api_reading_progress)
    app.router.add_post("/api/reading_ping", api_reading_ping)
    app.router.add_get("/api/home", api_home)
    app.router.add_post("/api/mark_onboarding_seen", api_mark_onboarding_seen)
    app.router.add_get("/api/check_force_sub", api_check_force_sub)
    app.router.add_post("/api/claim_daily_bonus", api_claim_daily_bonus)
    app.router.add_get("/api/tasks", api_tasks)
    app.router.add_post("/api/claim_task", api_claim_task)
    app.router.add_get("/api/reading_history", api_reading_history)
    app.router.add_get("/api/weekly_draw", api_weekly_draw)
    app.router.add_get("/api/books", api_books)
    app.router.add_get("/api/cover/{book_id}", api_book_cover)
    app.router.add_get("/api/draw_image/{draw_id}", api_draw_image)
    app.router.add_get("/api/avatar/{user_id}", api_avatar)
    app.router.add_get("/api/page_image/{book_id}/{page_number}", api_book_page_image)
    app.router.add_get("/api/page_data/{book_id}/{page_number}", api_book_page_data)
    app.router.add_post("/api/unlock_book", api_unlock_book)
    app.router.add_post("/api/book_earn", api_book_earn)
    app.router.add_get("/api/book_audio/{book_id}", api_book_audio_chapters)
    app.router.add_get("/api/book_audio_file/{book_id}/{chapter_number}", api_book_audio_file)
    app.router.add_post("/api/book_audio_progress", api_book_audio_progress)
    app.router.add_get("/api/profile_extra", api_profile_extra)
    app.router.add_post("/api/save_region", api_save_region)
    app.router.add_get("/api/shop_products", api_shop_products)
    app.router.add_get("/api/shop_image/{product_id}", api_shop_image)
    app.router.add_post("/api/buy_shop_product", api_buy_shop_product)
    app.router.add_get("/api/quizzes", api_quizzes)
    app.router.add_get("/api/quiz_questions/{quiz_id}", api_quiz_questions)
    app.router.add_get("/api/challenge", api_challenge)
    app.router.add_post("/api/buy_stars_premium", api_buy_stars_premium)
    app.router.add_post("/api/buy_stars_coins", api_buy_stars_coins)
    app.router.add_post("/api/challenge_join", api_challenge_join)
    app.router.add_get("/api/challenge_day/{day_number}", api_challenge_day)
    app.router.add_post("/api/challenge_day_submit", api_challenge_day_submit)
    app.router.add_get("/api/challenge_final", api_challenge_final)
    app.router.add_post("/api/challenge_final_submit", api_challenge_final_submit)
    app.router.add_get("/api/challenge_leaderboard", api_challenge_leaderboard)
    app.router.add_get("/api/verify_certificate", api_verify_certificate)
    app.router.add_get("/api/challenge_cover/{challenge_id}", api_challenge_cover)
    app.router.add_post("/api/quiz_submit", api_quiz_submit)
    app.router.add_get("/api/user_profile/{user_id}", api_user_profile)
    return app


# ================== ESLATMA ==================

async def reminder_loop():
    while True:
        try:
            current_hour = datetime.now().hour
            # Shaxsiylashtirilgan: har kim o'zining odatiy faol vaqtida eslatma oladi
            users = await db.get_users_for_reminder(inactive_days=REMINDER_INACTIVE_DAYS, target_hour=current_hour)
            for u in users:
                try:
                    text = random.choice(REMINDER_MESSAGES)
                    await bot.send_message(u["user_id"], text, reply_markup=webapp_keyboard())
                    await db.mark_reminder_sent(u["user_id"])
                    await asyncio.sleep(0.05)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Reminder loop xatosi: {e}")
        await asyncio.sleep(REMINDER_CHECK_INTERVAL_SECONDS)


async def premium_expiry_reminder_loop():
    """Premium a'zoligi ertaga (24 soat ichida) tugaydigan foydalanuvchilarga
    bir martalik eslatma yuboradi, va yana sotib olish tugmasini taklif qiladi."""
    while True:
        try:
            users = await db.get_users_for_premium_reminder()
            for u in users:
                try:
                    await bot.send_message(
                        u["user_id"],
                        "⏰ Premium a'zoligingiz ertaga tugaydi!\n\n"
                        "Barcha imtiyozlar (ko'proq koin, chegirmalar, maxsus kitoblar) "
                        "yo'qolib qolmasin — hoziroq uzaytiring 👇",
                        reply_markup=webapp_keyboard(),
                    )
                    await db.mark_premium_reminder_sent(u["user_id"])
                    await asyncio.sleep(0.05)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Premium eslatma loop xatosi: {e}")
        await asyncio.sleep(REMINDER_CHECK_INTERVAL_SECONDS)


# ================== ASOSIY ==================

async def main():
    os.makedirs(BOOK_STORAGE_DIR, exist_ok=True)
    os.makedirs(BOOK_TMP_DIR, exist_ok=True)

    await db.init_db()
    logger.info("Ma'lumotlar bazasiga ulandi (Postgres)")

    # Eslatma: standart tanlov endi avtomatik yaratilmaydi — admin uni /tanlov
    # buyrug'i orqali o'zi yaratadi. Shu tufayli /tanlov_ochir bilan o'chirilgan
    # tanlov qayta "tirilib" chiqmaydi.

    for ch in FORCE_CHANNELS:
        await db.seed_force_channel_if_empty(ch)
    logger.info("Majburiy obuna kanallari tayyor")

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook tozalandi, polling rejimida ishga tushmoqda")

    web_app = create_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info(f"Web server {PORT}-portda ishga tushdi")

    asyncio.create_task(reminder_loop())
    asyncio.create_task(premium_expiry_reminder_loop())
    logger.info("Eslatma (reminder) fon vazifasi ishga tushirildi")

    asyncio.create_task(process_pending_referrals_loop())
    logger.info("Kutilayotgan referallarni qayta ishlash fon vazifasi ishga tushirildi")

    logger.info(f"{APP_NAME} boti ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
