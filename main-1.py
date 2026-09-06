# main.py
# "Kitob Ovi" - Telegram Mini App backend
# aiogram 3 + aiohttp

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import re
import shutil
from urllib.parse import parse_qsl
from datetime import datetime

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
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import database as db
from pdf_tools import convert_pdf_to_page_images

# ================== SOZLAMALAR ==================

APP_NAME = "Kitobzor"

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

WEBAPP_URL = "https://web-production-aa006.up.railway.app"
PORT = int(os.environ.get("PORT", 8080))

COINS_PER_PAGE = 10
COINS_PER_PAGE_PREMIUM = 20
PAGE_TIMER_SECONDS = 30
PAGE_TIMER_SECONDS_PREMIUM = 15

REFERRAL_BONUS = 300
REFERRAL_MILESTONE_STEP = 5
REFERRAL_MILESTONE_PREMIUM_DAYS = 3

STREAK_BONUS_TABLE = {1: 5, 2: 10, 3: 15, 4: 25, 5: 35, 6: 50, 7: 100}

QUIZ_REWARD_PER_CORRECT = 15
QUIZ_REWARD_PER_CORRECT_PREMIUM = 22
MAX_QUIZ_QUESTIONS = 30

SHOP_DISCOUNT_PREMIUM = 0.20
DAILY_BONUS_PREMIUM = 50
PREMIUM_DURATION_DAYS = 7

FREE_DAILY_BONUS_AMOUNT = 20

DEFAULT_TANLOV_GOAL = 5
DEFAULT_TANLOV_DAYS = 20
DEFAULT_TANLOV_REWARD_TEXT = "500 koin sovg'a"

REMINDER_CHECK_INTERVAL_SECONDS = 6 * 3600
REMINDER_INACTIVE_DAYS = 1
REMINDER_MESSAGES = [
    "📖 Yangi bob sizni kutmoqda! Hoziroq o'qishni davom ettiring va koin yig'ing.",
    "🏹 Kitob Ovi sizni sog'indi! Bugun qancha koin yig'a olasiz?",
    "🔥 Streak seriyangizni uzmang — bugun kirib, bonusingizni oling!",
]

SHOP_ITEMS = {
    "badge": {"name": "Faxriy nishon (profilga)", "emoji": "🏅", "cost": 80, "type": "badge", "premium_only": False},
    "premium": {"name": "1 haftalik Premium a'zolik", "emoji": "⭐", "cost": 500, "type": "premium", "premium_only": False},
}

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

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

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
        f"💰 Koin qo'shish:\n"
        f"<code>/addcoins 500</code> — o'zingizga\n"
        f"<code>/addcoins 123456789 500</code> — boshqa foydalanuvchiga\n\n"
        f"⭐ Premium berish:\n"
        f"<code>/addpremium 123456789</code>\n\n"
        f"🏆 Tanlov shartini o'zgartirish:\n"
        f"<code>/tanlov 5 20 500 ming so'm sovg'a</code>\n"
        f"(referral soni, necha kunlik, sovg'a matni)\n"
        f"<code>/tanlov_holati</code> — joriy tanlov holati\n\n"
        f"📚 Yangi kitob qo'shish:\n"
        f"<code>/addbook</code> — bot ketma-ket so'raydi\n\n"
        f"✅ Yangi vazifa qo'shish:\n"
        f"<code>/addtask</code> — bot ketma-ket so'raydi\n\n"
        f"<code>/bekor</code> — istalgan jarayonni bekor qilish",
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
            "<code>/addcoins 500</code>\n"
            "<code>/addcoins 123456789 500</code>"
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
async def cmd_tanlov(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Bu buyruq faqat administratorlar uchun.")
        return

    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        await message.answer(
            "❗ Foydalanish:\n"
            "<code>/tanlov 5 20 500 ming so'm sovg'a</code>\n"
            "(1-son: referral soni, 2-son: necha kunlik, qolgani: sovg'a matni)"
        )
        return

    try:
        goal = int(parts[1])
        days = int(parts[2])
    except ValueError:
        await message.answer("❗ Referral soni va kun soni butun son bo'lishi kerak.")
        return

    reward_text = parts[3].strip()
    if not reward_text:
        await message.answer("❗ Sovg'a matnini kiriting.")
        return

    draw = await db.set_weekly_draw(goal, reward_text, days)
    await message.answer(
        f"✅ Tanlov yangilandi!\n\n"
        f"🎯 Shart: {goal} ta referral\n"
        f"🎁 Sovg'a: {reward_text}\n"
        f"📅 Muddat: {draw['week_start']} — {draw['week_end']} ({days} kun)"
    )


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
    today = datetime.now().strftime("%Y-%m-%d")
    days_left = "tugagan"
    try:
        end_dt = datetime.strptime(draw["week_end"], "%Y-%m-%d")
        remaining = (end_dt - datetime.now()).days
        days_left = f"{max(0, remaining)} kun qoldi"
    except Exception:
        pass

    await message.answer(
        f"🏆 <b>Joriy tanlov</b>\n\n"
        f"🎯 Shart: {draw['goal']} ta referral\n"
        f"🎁 Sovg'a: {draw['reward_text']}\n"
        f"📅 {draw['week_start']} — {draw['week_end']} ({days_left})\n"
        f"👥 Shartni bajarganlar: {participants} kishi"
    )


@dp.callback_query(F.data == "admin_randomizer")
async def callback_admin_randomizer(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q", show_alert=True)
        return

    winner = await db.get_random_eligible_winner(min_referrals=1)
    if not winner:
        await callback.answer("Mos g'olib topilmadi (hech kim referral qilmagan)", show_alert=True)
        return

    draw = await db.get_current_weekly_draw()
    if draw:
        week_label = f"{draw['week_start']} — {draw['week_end']}"
        prize = draw["reward_text"]
    else:
        week_label = datetime.now().strftime("%Y-%m-%d")
        prize = "Sovg'a"

    await db.add_winner(winner["user_id"], winner["username"], week_label, winner["cnt"], prize)

    await callback.message.answer(
        f"🎉 <b>G'olib aniqlandi!</b>\n\n"
        f"👤 {winner['username'] or winner['user_id']}\n"
        f"👥 Referral soni: {winner['cnt']}\n"
        f"🎁 Sovg'a: {prize}\n"
        f"📅 Muddat: {week_label}\n\n"
        f"Natija 'So'nggi g'oliblar' ro'yxatiga avtomatik saqlandi."
    )

    try:
        await bot.send_message(
            winner["user_id"],
            f"🎉 Tabriklaymiz! Siz tanlovda g'olib bo'ldingiz!\n🎁 Sovg'a: {prize}"
        )
    except Exception:
        pass

    await callback.answer("✅ G'olib aniqlandi!")


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


@dp.message(AddBookStates.pdf, F.document)
async def process_book_pdf(message: Message, state: FSMContext):
    doc = message.document
    filename = (doc.file_name or "").lower()
    is_pdf = (doc.mime_type == "application/pdf") or filename.endswith(".pdf")
    if not is_pdf:
        await message.answer("❗ Iltimos, PDF formatidagi faylni yuboring.")
        return

    data = await state.get_data()
    tmp_dir = data["tmp_dir"]
    pdf_path = os.path.join(tmp_dir, "book.pdf")

    await message.answer("⏳ PDF yuklanmoqda va sahifalarga aylantirilmoqda, biroz kuting...")
    await bot.download(doc, destination=pdf_path)

    pages_dir = os.path.join(tmp_dir, "pages")
    try:
        page_paths = await asyncio.to_thread(convert_pdf_to_page_images, pdf_path, pages_dir)
    except Exception as e:
        logger.exception("PDF konvertatsiya xatosi")
        await message.answer(
            f"❌ PDF'ni qayta ishlashda xatolik yuz berdi: {e}\n"
            f"Boshqa PDF fayl bilan urinib ko'ring, yoki /bekor bilan to'xtating."
        )
        return

    if not page_paths:
        await message.answer("❗ PDF'dan hech qanday sahifa chiqmadi. Boshqa fayl yuboring, yoki /bekor.")
        return

    await state.update_data(pdf_page_count=len(page_paths))
    await state.set_state(AddBookStates.referrals)
    await message.answer(
        f"✅ {len(page_paths)} ta sahifaga aylantirildi.\n\n"
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
        "🪙 Bu kitobni ochish uchun nechta koin kerak? (shart bo'lmasa 0 yozing):"
    )


@dp.message(AddBookStates.coins)
async def process_book_coins(message: Message, state: FSMContext):
    try:
        coins_required = int((message.text or "").strip())
        if coins_required < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Iltimos, butun son yuboring (masalan: 0 yoki 100).")
        return

    data = await state.get_data()
    tmp_dir = data["tmp_dir"]
    title = data["title"]
    author = data["author"]
    genre = data["genre"]
    cover_tmp_path = data["cover_tmp_path"]
    required_referrals = data["required_referrals"]
    pages_tmp_dir = os.path.join(tmp_dir, "pages")

    await message.answer("💾 Saqlanmoqda...")

    try:
        book_id = await db.add_book(title, author, genre, "", required_referrals, coins_required)

        final_dir = os.path.join(BOOK_STORAGE_DIR, str(book_id))
        final_pages_dir = os.path.join(final_dir, "pages")
        os.makedirs(final_pages_dir, exist_ok=True)

        final_cover_rel = f"{book_id}/cover.jpg"
        final_cover_abs = os.path.join(BOOK_STORAGE_DIR, final_cover_rel)
        shutil.move(cover_tmp_path, final_cover_abs)

        page_files = sorted(
            os.listdir(pages_tmp_dir),
            key=lambda name: int(name.split("_")[1].split(".")[0])
        )
        final_page_rel_paths = []
        for name in page_files:
            src = os.path.join(pages_tmp_dir, name)
            dst_rel = f"{book_id}/pages/{name}"
            dst_abs = os.path.join(BOOK_STORAGE_DIR, dst_rel)
            shutil.move(src, dst_abs)
            final_page_rel_paths.append(dst_rel)

        await db.set_book_cover_path(book_id, final_cover_rel)
        await db.add_book_pages(book_id, final_page_rel_paths)

        shutil.rmtree(tmp_dir, ignore_errors=True)
        await state.clear()

        await message.answer(
            f"✅ Kitob muvaffaqiyatli qo'shildi!\n\n"
            f"📕 {title}\n"
            f"✍️ {author}\n"
            f"🏷 {genre}\n"
            f"📄 {len(final_page_rel_paths)} sahifa\n"
            f"👥 Kerak: {required_referrals} referral, 🪙 {coins_required} koin"
        )
    except Exception as e:
        logger.exception("Kitobni saqlashda xato")
        await message.answer(f"❌ Kitobni saqlashda xatolik yuz berdi: {e}")
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
    streak = await db.update_streak(user_id)

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
        "referral_count": referral_count,
        "streak_bonus_granted": granted,
        "daily_bonus_granted": daily_granted,
        "page_timer": PAGE_TIMER_SECONDS_PREMIUM if is_premium else PAGE_TIMER_SECONDS,
        "coins_per_page": COINS_PER_PAGE_PREMIUM if is_premium else COINS_PER_PAGE,
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
    earned = correct * reward_per_correct

    new_balance = await db.add_coins(user_id, earned)
    await db.increment_quiz_correct_total(user_id, correct)

    return web.json_response({
        "success": True,
        "coins": new_balance,
        "earned": earned,
        "is_premium": is_premium
    })


async def api_leaderboard(request: web.Request):
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

    result = []
    for key, chest in CHESTS.items():
        opened = await db.get_chest_opens_today(user_id, key)
        result.append({
            "id": key,
            "name": chest["name"],
            "emoji": chest["emoji"],
            "cost": chest["cost"],
            "daily_limit": chest["daily_limit"],
            "opened_today": opened,
            "remaining": max(0, chest["daily_limit"] - opened),
            "can_open": opened < chest["daily_limit"] and user["coins"] >= chest["cost"]
        })

    return web.json_response({"chests": result, "coins": user["coins"]})


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
    if chest_id not in CHESTS:
        return web.json_response({"error": "invalid chest"}, status=400)

    chest = CHESTS[chest_id]
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
    else:
        days = chosen["days"]
        await db.activate_premium(user_id, days=days)
        await db.record_chest_open(user_id, chest_id, "premium", days)
        reward_text = f"+{days} kun Premium"
        reward_type = "premium"
        reward_amount = days

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

async def api_home(request: web.Request):
    init_data = request.query.get("initData", "")
    user_data = validate_init_data(init_data)
    if not user_data:
        return web.json_response({"error": "unauthorized"}, status=401)

    user_id = user_data["id"]
    username = user_data.get("username") or user_data.get("first_name", "User")
    await db.create_user_if_missing(user_id, username)

    user = await db.get_user(user_id)
    is_premium = await db.check_premium_status(user_id)
    referral_count = await db.get_referral_count(user_id)
    referral_rank = await db.get_referral_rank(user_id)
    free_bonus_claimed = await db.has_claimed_free_daily_bonus(user_id)

    tasks = await db.get_tasks_with_progress(user_id)
    unclaimed_tasks = sum(1 for t in tasks if t["completed"] and not t["claimed"])

    draw = await db.ensure_default_weekly_draw(DEFAULT_TANLOV_GOAL, DEFAULT_TANLOV_REWARD_TEXT, DEFAULT_TANLOV_DAYS)
    weekly_info = None
    if draw:
        weekly_progress = await db.get_weekly_referral_progress(user_id, draw["week_start"])
        weekly_info = {
            "goal": draw["goal"],
            "reward_text": draw["reward_text"],
            "progress": min(weekly_progress, draw["goal"]),
            "week_start": draw["week_start"],
            "week_end": draw["week_end"],
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
        "coins": user["coins"],
        "referral_count": referral_count,
        "referral_rank": referral_rank,
        "streak_days": user["streak_days"] or 0,
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
            "book_title": r["book_title"] or "Kitob Ovi",
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
    draw = await db.ensure_default_weekly_draw(DEFAULT_TANLOV_GOAL, DEFAULT_TANLOV_REWARD_TEXT, DEFAULT_TANLOV_DAYS)
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

    result = []
    for b in books:
        unlocked = b["id"] in unlocked_ids
        progress = await db.get_book_progress(user_id, b["id"]) if unlocked else 0
        result.append({
            "id": b["id"],
            "title": b["title"],
            "author": b["author"],
            "genre": b["genre"],
            "cover_url": f"/api/cover/{b['id']}" if b["cover_path"] else None,
            "page_count": b["page_count"],
            "required_referrals": b["required_referrals"],
            "required_coins": b["required_coins"],
            "unlocked": unlocked,
            "current_page": progress,
        })

    return web.json_response({
        "books": result,
        "referral_count": referral_count,
        "coins": user["coins"],
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

    if referral_count < book["required_referrals"]:
        return web.json_response({
            "error": "not_enough_referrals",
            "required_referrals": book["required_referrals"],
            "current_referrals": referral_count,
        }, status=400)

    if user["coins"] < book["required_coins"]:
        return web.json_response({
            "error": "not_enough_coins",
            "required_coins": book["required_coins"],
            "current_coins": user["coins"],
        }, status=400)

    new_balance = user["coins"]
    if book["required_coins"] > 0:
        new_balance = await db.add_coins(user_id, -book["required_coins"])

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
    amount = COINS_PER_PAGE_PREMIUM if is_premium else COINS_PER_PAGE

    earned = 0
    if not already_read:
        await db.mark_page_read(user_id, book_id, page_number)
        await db.add_coins(user_id, amount)
        await db.increment_pages_read(user_id)
        await db.record_reading_event(user_id, book["title"], page_number, amount)
        earned = amount

    await db.set_book_progress(user_id, book_id, page_number)
    user = await db.get_user(user_id)

    return web.json_response({
        "success": True,
        "earned": earned,
        "coins": user["coins"],
        "already_read": already_read,
    })


async def index_page(request: web.Request):
    return web.FileResponse("./index.html")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index_page)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/shop_items", api_shop_items)
    app.router.add_post("/api/purchase", api_purchase)
    app.router.add_get("/api/my_items", api_my_items)
    app.router.add_post("/api/quiz_complete", api_quiz_complete)
    app.router.add_get("/api/leaderboard", api_leaderboard)
    app.router.add_get("/api/chests", api_chests)
    app.router.add_post("/api/open_chest", api_open_chest)
    app.router.add_get("/api/home", api_home)
    app.router.add_post("/api/claim_daily_bonus", api_claim_daily_bonus)
    app.router.add_get("/api/tasks", api_tasks)
    app.router.add_post("/api/claim_task", api_claim_task)
    app.router.add_get("/api/reading_history", api_reading_history)
    app.router.add_get("/api/weekly_draw", api_weekly_draw)
    app.router.add_get("/api/books", api_books)
    app.router.add_get("/api/cover/{book_id}", api_book_cover)
    app.router.add_get("/api/page_image/{book_id}/{page_number}", api_book_page_image)
    app.router.add_post("/api/unlock_book", api_unlock_book)
    app.router.add_post("/api/book_earn", api_book_earn)
    return app


# ================== ESLATMA ==================

async def reminder_loop():
    while True:
        try:
            users = await db.get_users_for_reminder(inactive_days=REMINDER_INACTIVE_DAYS)
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


# ================== ASOSIY ==================

async def main():
    os.makedirs(BOOK_STORAGE_DIR, exist_ok=True)
    os.makedirs(BOOK_TMP_DIR, exist_ok=True)

    await db.init_db()
    logger.info("Ma'lumotlar bazasiga ulandi (Postgres)")

    await db.ensure_default_weekly_draw(DEFAULT_TANLOV_GOAL, DEFAULT_TANLOV_REWARD_TEXT, DEFAULT_TANLOV_DAYS)
    logger.info("Tanlov tayyor")

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
