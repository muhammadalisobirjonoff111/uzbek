"""
Yagona fayl — ikkala vazifani ham bajaradi:
1) Kunlik vazifa eslatmalari
2) Raqobatchi Telegram kanallarini kuzatish
3) Kunlik reels ssenariylari (100 kunlik challenge uchun)

Bot to'liq tugmalar (menyu) orqali boshqariladi — buyruq yozish shart emas,
lekin eski / buyruqlar ham parallel ishlayveradi.

Ishga tushirish:  python main.py
"""
import asyncio
import logging
import os
import random
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bs4 import BeautifulSoup
from dotenv import load_dotenv

import database as db
from content_bank import SCENARIOS

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")
COMPETITOR_CHECK_MINUTES = 5  # har necha daqiqada kanallarni tekshirish

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone=TIMEZONE)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
TZ = ZoneInfo(TIMEZONE)

TIME_PRESETS = ["06:00", "07:00", "08:00", "09:00", "12:00", "18:00", "20:00"]
COUNT_PRESETS = [5, 10, 15, 20, 30, 40]


def now_hhmm() -> str:
    """Belgilangan vaqt zonasi (masalan Asia/Tashkent) bo'yicha joriy vaqtni HH:MM formatida qaytaradi."""
    return datetime.now(TZ).strftime("%H:%M")


def today_local() -> date:
    return datetime.now(TZ).date()


async def safe_edit(message: Message, text: str, reply_markup=None, parse_mode: str = "HTML"):
    """edit_text ni chaqiradi; agar matn/tugmalar avvalgisi bilan bir xil bo'lsa,
    Telegram beradigan zararsiz xatolikni e'tiborsiz qoldiradi."""
    try:
        await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


class Form(StatesGroup):
    waiting_task_text = State()
    waiting_competitor_username = State()
    waiting_custom_scenario_time = State()
    waiting_custom_reminder_time = State()


# ---------------- KEYBOARDS ----------------

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎬 Ssenariylar"), KeyboardButton(text="✅ Vazifalar")],
            [KeyboardButton(text="👀 Raqobatchilar"), KeyboardButton(text="📅 Challenge")],
            [KeyboardButton(text="ℹ️ Yordam")],
        ],
        resize_keyboard=True,
    )


def scenarios_menu_kb(user) -> InlineKeyboardMarkup:
    toggle_text = "🔕 O'chirish" if user and user["scenarios_on"] else "🔔 Yoqish"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Hozir olish", callback_data="scn_now")],
        [InlineKeyboardButton(text="⏰ Kunlik vaqtni belgilash", callback_data="scn_time_menu")],
        [InlineKeyboardButton(text="🔢 Kunlik sonini belgilash", callback_data="scn_count_menu")],
        [InlineKeyboardButton(text=toggle_text, callback_data="scn_toggle")],
    ])


def time_picker_kb(prefix: str) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for i, t in enumerate(TIME_PRESETS, start=1):
        row.append(InlineKeyboardButton(text=t, callback_data=f"{prefix}:{t}"))
        if i % 3 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✏️ Boshqa vaqt yozish", callback_data=f"{prefix}:custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def count_picker_kb() -> InlineKeyboardMarkup:
    rows = []
    row = []
    for i, c in enumerate(COUNT_PRESETS, start=1):
        row.append(InlineKeyboardButton(text=str(c), callback_data=f"scn_count:{c}"))
        if i % 3 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tasks_menu_kb(user) -> InlineKeyboardMarkup:
    toggle_text = "🔕 Eslatmani o'chirish" if user and user["reminders_on"] else "🔔 Eslatmani yoqish"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Ro'yxatni ko'rish", callback_data="task_list")],
        [InlineKeyboardButton(text="➕ Vazifa qo'shish", callback_data="task_add")],
        [InlineKeyboardButton(text="🗑 Vazifani o'chirish", callback_data="task_del_menu")],
        [InlineKeyboardButton(text="🧹 Hammasini tozalash", callback_data="task_clear")],
        [InlineKeyboardButton(text="⏰ Eslatma vaqti", callback_data="task_time_menu")],
        [InlineKeyboardButton(text=toggle_text, callback_data="task_toggle")],
    ])


def competitors_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👀 Ro'yxatni ko'rish", callback_data="comp_list")],
        [InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="comp_add")],
        [InlineKeyboardButton(text="🗑 Kanalni o'chirish", callback_data="comp_del_menu")],
    ])


def challenge_menu_kb(user) -> InlineKeyboardMarkup:
    buttons = []
    if user and user["challenge_start_date"]:
        buttons.append([InlineKeyboardButton(text="📅 Nechanchi kundaman?", callback_data="chal_day")])
    else:
        buttons.append([InlineKeyboardButton(text="🏁 Challengeni boshlash", callback_data="chal_start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_main")]
    ])


HELP_TEXT = (
    "🤖 <b>Botdan qanday foydalanish</b>\n\n"
    "Pastdagi menyudan kerakli bo'limni bosing — hamma narsa tugmalar orqali qilinadi:\n\n"
    "🎬 <b>Ssenariylar</b> — kunlik reels g'oyalari, vaqt va son sozlamalari\n"
    "✅ <b>Vazifalar</b> — kunlik ish vazifalari va eslatma\n"
    "👀 <b>Raqobatchilar</b> — Telegram kanallarini kuzatish\n"
    "📅 <b>Challenge</b> — 100 kunlik challenge kun hisoblagichi\n\n"
    "Istasangiz, eski buyruqlarni yozib ham foydalanishingiz mumkin: /scenarios, "
    "/addtask, /addcompetitor va h.k. — /help orqali to'liq ro'yxatni ko'rasiz."
)


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    db.add_user(message.chat.id, message.from_user.username)
    await message.answer(
        "Salom! 👋\n\n"
        "Men sizga uchta narsada yordam beraman:\n\n"
        "1️⃣ Har kuni tayyor reels ssenariylarini yuboraman — bosh qotirmasdan tanlab suratga olasiz\n"
        "2️⃣ Ish vazifalaringizni eslatib turaman\n"
        "3️⃣ Raqobatchilaringizning Telegram kanallarini kuzatib, yangilik chiqsa xabar beraman\n\n"
        "Pastdagi menyudan boshlang 👇",
        reply_markup=main_menu_kb(),
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=main_menu_kb())


@dp.message(F.text == "ℹ️ Yordam")
async def btn_help(message: Message):
    await cmd_help(message)


# ==================== SSENARIYLAR ====================

def pick_scenarios(chat_id: int, count: int) -> list[dict]:
    total = len(SCENARIOS)
    count = min(count, total)
    sent = db.get_sent_indices(chat_id)
    available = [i for i in range(total) if i not in sent]

    if len(available) < count:
        db.reset_sent_scenarios(chat_id)
        available = list(range(total))

    chosen = random.sample(available, count)
    db.mark_scenarios_sent(chat_id, chosen)
    return [SCENARIOS[i] for i in chosen]


def format_scenarios(chat_id: int, scenarios: list[dict]) -> str:
    user = db.get_user(chat_id)
    header = "🎬 <b>Bugungi reels ssenariylari</b>"
    if user and user["challenge_start_date"]:
        start = date.fromisoformat(user["challenge_start_date"])
        day_num = (today_local() - start).days + 1
        header = f"🎬 <b>100 kunlik challenge — Kun {day_num}/100</b>\nBugungi ssenariylar:"

    blocks = [header, ""]
    for i, s in enumerate(scenarios, start=1):
        blocks.append(
            f"<b>{i}. {s['title']}</b>\n"
            f"🎯 Hook: {s['hook']}\n"
            f"📋 {s['body']}\n"
            f"📣 CTA: {s['cta']}\n"
        )
    blocks.append("Yoqqan bittasini tanlang, suratga oling va joylang. Omad! 🚀")
    return "\n".join(blocks)


async def send_scenarios_menu(chat_id: int, edit_message: Message | None = None):
    user = db.get_user(chat_id)
    time_str = user["scenario_time"] if user else "06:00"
    count_str = user["scenario_count"] if user else 10
    status = "yoqilgan ✅" if user and user["scenarios_on"] else "o'chirilgan ⛔"
    text = (
        "🎬 <b>Kunlik reels ssenariylari</b>\n\n"
        f"⏰ Kunlik vaqt: <b>{time_str}</b>\n"
        f"🔢 Kuniga son: <b>{count_str}</b> ta\n"
        f"Holat: {status}"
    )
    kb = scenarios_menu_kb(user)
    if edit_message:
        await safe_edit(edit_message, text, reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


@dp.message(Command("scenarios"))
async def cmd_scenarios(message: Message):
    user = db.get_user(message.chat.id)
    count = user["scenario_count"] if user else 10
    scenarios = pick_scenarios(message.chat.id, count)
    await message.answer(format_scenarios(message.chat.id, scenarios), parse_mode="HTML")


@dp.message(F.text == "🎬 Ssenariylar")
async def btn_scenarios(message: Message):
    db.add_user(message.chat.id, message.from_user.username)
    await send_scenarios_menu(message.chat.id)


@dp.callback_query(F.data == "scn_now")
async def cb_scn_now(callback: CallbackQuery):
    user = db.get_user(callback.message.chat.id)
    count = user["scenario_count"] if user else 10
    scenarios = pick_scenarios(callback.message.chat.id, count)
    await callback.message.answer(format_scenarios(callback.message.chat.id, scenarios), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "scn_time_menu")
async def cb_scn_time_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "⏰ Kunlik ssenariylar qaysi vaqtda kelsin?",
        reply_markup=time_picker_kb("scn_time"),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("scn_time:"))
async def cb_scn_time_pick(callback: CallbackQuery, state: FSMContext):
    value = callback.data.split(":", 1)[1]
    if value == "custom":
        await state.set_state(Form.waiting_custom_scenario_time)
        await callback.message.edit_text("✏️ Vaqtni SS:DD formatida yozing (masalan 06:30):")
        await callback.answer()
        return
    db.set_scenario_time(callback.message.chat.id, value)
    db.toggle_scenarios(callback.message.chat.id, True)
    await callback.answer(f"⏰ {value} ga o'rnatildi")
    await send_scenarios_menu(callback.message.chat.id, edit_message=callback.message)


@dp.message(Form.waiting_custom_scenario_time)
async def receive_custom_scenario_time(message: Message, state: FSMContext):
    value = message.text.strip()
    if not TIME_RE.match(value):
        await message.answer("Noto'g'ri format. Masalan: 06:30")
        return
    db.set_scenario_time(message.chat.id, value)
    db.toggle_scenarios(message.chat.id, True)
    await state.clear()
    await message.answer(f"⏰ Kunlik ssenariylar endi soat {value} da keladi.")
    await send_scenarios_menu(message.chat.id)


@dp.callback_query(F.data == "scn_count_menu")
async def cb_scn_count_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "🔢 Kuniga nechta ssenariy kerak?",
        reply_markup=count_picker_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("scn_count:"))
async def cb_scn_count_pick(callback: CallbackQuery):
    value = int(callback.data.split(":", 1)[1])
    db.set_scenario_count(callback.message.chat.id, value)
    await callback.answer(f"✅ Kuniga {value} ta")
    await send_scenarios_menu(callback.message.chat.id, edit_message=callback.message)


@dp.callback_query(F.data == "scn_toggle")
async def cb_scn_toggle(callback: CallbackQuery):
    user = db.get_user(callback.message.chat.id)
    new_state = not (user and user["scenarios_on"])
    db.toggle_scenarios(callback.message.chat.id, new_state)
    await callback.answer("🔔 Yoqildi" if new_state else "🔕 O'chirildi")
    await send_scenarios_menu(callback.message.chat.id, edit_message=callback.message)


# Eski buyruqlar (parallel ishlaydi)
@dp.message(Command("scenariotime"))
async def cmd_scenariotime(message: Message):
    arg = message.text.partition(" ")[2].strip()
    if not TIME_RE.match(arg):
        await message.answer("Foydalanish: /scenariotime 06:00")
        return
    db.set_scenario_time(message.chat.id, arg)
    db.toggle_scenarios(message.chat.id, True)
    await message.answer(f"⏰ Kunlik ssenariylar endi har kuni soat {arg} da keladi.")


@dp.message(Command("scenariocount"))
async def cmd_scenariocount(message: Message):
    arg = message.text.partition(" ")[2].strip()
    if not arg.isdigit() or not (1 <= int(arg) <= len(SCENARIOS)):
        await message.answer(f"Foydalanish: /scenariocount 10  (1 dan {len(SCENARIOS)} gacha)")
        return
    db.set_scenario_count(message.chat.id, int(arg))
    await message.answer(f"✅ Kuniga {arg} ta ssenariy yuboriladi.")


@dp.message(Command("scenarioson"))
async def cmd_scenarioson(message: Message):
    db.toggle_scenarios(message.chat.id, True)
    await message.answer("🔔 Kunlik ssenariylar yoqildi.")


@dp.message(Command("scenariosoff"))
async def cmd_scenariosoff(message: Message):
    db.toggle_scenarios(message.chat.id, False)
    await message.answer("🔕 Kunlik ssenariylar o'chirildi.")


async def check_and_send_scenarios():
    now_str = now_hhmm()
    for user in db.get_all_users():
        if not user["scenarios_on"]:
            continue
        if user["scenario_time"] != now_str:
            continue
        scenarios = pick_scenarios(user["chat_id"], user["scenario_count"])
        text = format_scenarios(user["chat_id"], scenarios)
        try:
            await bot.send_message(user["chat_id"], text, parse_mode="HTML")
        except Exception as e:
            log.warning(f"Ssenariy yuborilmadi {user['chat_id']}: {e}")


# ==================== CHALLENGE ====================

async def send_challenge_menu(chat_id: int, edit_message: Message | None = None):
    user = db.get_user(chat_id)
    if user and user["challenge_start_date"]:
        start = date.fromisoformat(user["challenge_start_date"])
        day_num = (today_local() - start).days + 1
        text = f"📅 <b>100 kunlik challenge</b>\n\nSiz hozir {day_num}/100-kundasiz. Omad! 💪"
    else:
        text = "📅 <b>100 kunlik challenge</b>\n\nHali boshlanmagan. Boshlashga tayyormisiz?"
    kb = challenge_menu_kb(user)
    if edit_message:
        await safe_edit(edit_message, text, reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "📅 Challenge")
async def btn_challenge(message: Message):
    db.add_user(message.chat.id, message.from_user.username)
    await send_challenge_menu(message.chat.id)


@dp.callback_query(F.data == "chal_start")
async def cb_chal_start(callback: CallbackQuery):
    db.start_challenge(callback.message.chat.id, today_local().isoformat())
    await callback.answer("🏁 Challenge boshlandi!")
    await send_challenge_menu(callback.message.chat.id, edit_message=callback.message)


@dp.callback_query(F.data == "chal_day")
async def cb_chal_day(callback: CallbackQuery):
    await send_challenge_menu(callback.message.chat.id, edit_message=callback.message)
    await callback.answer()


@dp.message(Command("startchallenge"))
async def cmd_startchallenge(message: Message):
    db.start_challenge(message.chat.id, today_local().isoformat())
    await message.answer("🏁 100 kunlik challenge boshlandi! Kun 1/100.")


@dp.message(Command("challengeday"))
async def cmd_challengeday(message: Message):
    user = db.get_user(message.chat.id)
    if not user or not user["challenge_start_date"]:
        await message.answer("Challenge hali boshlanmagan. /startchallenge bilan boshlang.")
        return
    start = date.fromisoformat(user["challenge_start_date"])
    day_num = (today_local() - start).days + 1
    await message.answer(f"📅 Siz hozir {day_num}/100-kundasiz.")


# ==================== VAZIFALAR ====================

async def send_tasks_menu(chat_id: int, edit_message: Message | None = None):
    user = db.get_user(chat_id)
    time_str = user["reminder_time"] if user else "09:00"
    status = "yoqilgan ✅" if user and user["reminders_on"] else "o'chirilgan ⛔"
    text = (
        "✅ <b>Kunlik vazifalar</b>\n\n"
        f"⏰ Eslatma vaqti: <b>{time_str}</b>\n"
        f"Holat: {status}"
    )
    kb = tasks_menu_kb(user)
    if edit_message:
        await safe_edit(edit_message, text, reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "✅ Vazifalar")
async def btn_tasks(message: Message):
    db.add_user(message.chat.id, message.from_user.username)
    await send_tasks_menu(message.chat.id)


@dp.callback_query(F.data == "task_list")
async def cb_task_list(callback: CallbackQuery):
    tasks = db.get_tasks(callback.message.chat.id)
    if not tasks:
        await callback.answer("Hozircha vazifalar yo'q", show_alert=True)
        return
    lines = [f"{t['id']}. {t['text']}" for t in tasks]
    await callback.message.answer("📋 <b>Vazifalaringiz:</b>\n" + "\n".join(lines), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "task_add")
async def cb_task_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_task_text)
    await callback.message.answer("➕ Yangi vazifa matnini yozing:")
    await callback.answer()


@dp.message(Form.waiting_task_text)
async def receive_task_text(message: Message, state: FSMContext):
    db.add_task(message.chat.id, message.text.strip())
    await state.clear()
    await message.answer(f"✅ Qo'shildi: {message.text.strip()}")
    await send_tasks_menu(message.chat.id)


@dp.callback_query(F.data == "task_del_menu")
async def cb_task_del_menu(callback: CallbackQuery):
    tasks = db.get_tasks(callback.message.chat.id)
    if not tasks:
        await callback.answer("Vazifalar yo'q", show_alert=True)
        return
    rows = [[InlineKeyboardButton(text=f"🗑 {t['text'][:30]}", callback_data=f"task_del:{t['id']}")] for t in tasks]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="task_back")])
    await callback.message.edit_text("Qaysi vazifani o'chiramiz?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@dp.callback_query(F.data.startswith("task_del:"))
async def cb_task_del(callback: CallbackQuery):
    task_id = int(callback.data.split(":", 1)[1])
    db.delete_task(callback.message.chat.id, task_id)
    await callback.answer("🗑 O'chirildi")
    await send_tasks_menu(callback.message.chat.id, edit_message=callback.message)


@dp.callback_query(F.data == "task_clear")
async def cb_task_clear(callback: CallbackQuery):
    db.clear_tasks(callback.message.chat.id)
    await callback.answer("🧹 Tozalandi")
    await send_tasks_menu(callback.message.chat.id, edit_message=callback.message)


@dp.callback_query(F.data == "task_time_menu")
async def cb_task_time_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "⏰ Vazifa eslatmasi qaysi vaqtda kelsin?",
        reply_markup=time_picker_kb("task_time"),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("task_time:"))
async def cb_task_time_pick(callback: CallbackQuery, state: FSMContext):
    value = callback.data.split(":", 1)[1]
    if value == "custom":
        await state.set_state(Form.waiting_custom_reminder_time)
        await callback.message.edit_text("✏️ Vaqtni SS:DD formatida yozing (masalan 09:30):")
        await callback.answer()
        return
    db.set_reminder_time(callback.message.chat.id, value)
    db.toggle_reminders(callback.message.chat.id, True)
    await callback.answer(f"⏰ {value} ga o'rnatildi")
    await send_tasks_menu(callback.message.chat.id, edit_message=callback.message)


@dp.message(Form.waiting_custom_reminder_time)
async def receive_custom_reminder_time(message: Message, state: FSMContext):
    value = message.text.strip()
    if not TIME_RE.match(value):
        await message.answer("Noto'g'ri format. Masalan: 09:30")
        return
    db.set_reminder_time(message.chat.id, value)
    db.toggle_reminders(message.chat.id, True)
    await state.clear()
    await message.answer(f"⏰ Vazifa eslatmasi endi soat {value} da keladi.")
    await send_tasks_menu(message.chat.id)


@dp.callback_query(F.data == "task_toggle")
async def cb_task_toggle(callback: CallbackQuery):
    user = db.get_user(callback.message.chat.id)
    new_state = not (user and user["reminders_on"])
    db.toggle_reminders(callback.message.chat.id, new_state)
    await callback.answer("🔔 Yoqildi" if new_state else "🔕 O'chirildi")
    await send_tasks_menu(callback.message.chat.id, edit_message=callback.message)


@dp.callback_query(F.data == "task_back")
async def cb_task_back(callback: CallbackQuery):
    await send_tasks_menu(callback.message.chat.id, edit_message=callback.message)
    await callback.answer()


# Eski buyruqlar
@dp.message(Command("addtask"))
async def cmd_addtask(message: Message):
    text = message.text.partition(" ")[2].strip()
    if not text:
        await message.answer("Foydalanish: /addtask Instagram uchun 3 ta post tayyorlash")
        return
    db.add_task(message.chat.id, text)
    await message.answer(f"✅ Qo'shildi: {text}")


@dp.message(Command("mytasks"))
async def cmd_mytasks(message: Message):
    tasks = db.get_tasks(message.chat.id)
    if not tasks:
        await message.answer("Hozircha vazifalar yo'q. /addtask bilan qo'shing.")
        return
    lines = [f"{t['id']}. {t['text']}" for t in tasks]
    await message.answer("📋 <b>Vazifalaringiz:</b>\n" + "\n".join(lines), parse_mode="HTML")


@dp.message(Command("deltask"))
async def cmd_deltask(message: Message):
    arg = message.text.partition(" ")[2].strip()
    if not arg.isdigit():
        await message.answer("Foydalanish: /deltask 3")
        return
    ok = db.delete_task(message.chat.id, int(arg))
    await message.answer("🗑 O'chirildi." if ok else "Bunday raqamli vazifa topilmadi.")


@dp.message(Command("cleartasks"))
async def cmd_cleartasks(message: Message):
    db.clear_tasks(message.chat.id)
    await message.answer("🗑 Barcha vazifalar tozalandi.")


@dp.message(Command("settime"))
async def cmd_settime(message: Message):
    arg = message.text.partition(" ")[2].strip()
    if not TIME_RE.match(arg):
        await message.answer("Foydalanish: /settime 09:00")
        return
    db.set_reminder_time(message.chat.id, arg)
    await message.answer(f"⏰ Eslatma vaqti {arg} ga o'rnatildi.")


@dp.message(Command("remindon"))
async def cmd_remindon(message: Message):
    db.toggle_reminders(message.chat.id, True)
    await message.answer("🔔 Kunlik eslatma yoqildi.")


@dp.message(Command("remindoff"))
async def cmd_remindoff(message: Message):
    db.toggle_reminders(message.chat.id, False)
    await message.answer("🔕 Kunlik eslatma o'chirildi.")


async def check_and_send_reminders():
    now_str = now_hhmm()
    for user in db.get_all_users():
        if not user["reminders_on"]:
            continue
        if user["reminder_time"] != now_str:
            continue
        tasks = db.get_tasks(user["chat_id"])
        if not tasks:
            text = "☀️ Xayrli tong! Bugunga hali vazifa qo'shmagansiz."
        else:
            lines = [f"☐ {t['text']}" for t in tasks]
            text = "☀️ <b>Bugungi vazifalaringiz:</b>\n" + "\n".join(lines)
        try:
            await bot.send_message(user["chat_id"], text, parse_mode="HTML")
        except Exception as e:
            log.warning(f"Reminder yuborilmadi {user['chat_id']}: {e}")


# ==================== RAQOBATCHILAR ====================

async def send_competitors_menu(chat_id: int, edit_message: Message | None = None):
    text = "👀 <b>Raqobatchilarni kuzatish</b>\n\nOchiq Telegram kanallarini qo'shing — yangi post chiqsa xabar beraman."
    kb = competitors_menu_kb()
    if edit_message:
        await safe_edit(edit_message, text, reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "👀 Raqobatchilar")
async def btn_competitors(message: Message):
    db.add_user(message.chat.id, message.from_user.username)
    await send_competitors_menu(message.chat.id)


@dp.callback_query(F.data == "comp_list")
async def cb_comp_list(callback: CallbackQuery):
    comps = db.get_competitors(callback.message.chat.id)
    if not comps:
        await callback.answer("Kuzatilayotgan kanal yo'q", show_alert=True)
        return
    lines = [f"• @{c['channel_username']}" for c in comps]
    await callback.message.answer("👀 <b>Kuzatilayotgan kanallar:</b>\n" + "\n".join(lines), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "comp_add")
async def cb_comp_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_competitor_username)
    await callback.message.answer("➕ Kanal usernameni yozing (masalan @kanal_nomi):")
    await callback.answer()


@dp.message(Form.waiting_competitor_username)
async def receive_competitor_username(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@").lower()
    exists = await channel_exists(username)
    await state.clear()
    if not exists:
        await message.answer("⚠️ Bu kanal topilmadi yoki yopiq. Faqat ochiq kanallarni kuzatish mumkin.")
        return
    ok = db.add_competitor(message.chat.id, username)
    await message.answer(f"✅ @{username} qo'shildi." if ok else "Bu kanal allaqachon ro'yxatda bor.")
    await send_competitors_menu(message.chat.id)


@dp.callback_query(F.data == "comp_del_menu")
async def cb_comp_del_menu(callback: CallbackQuery):
    comps = db.get_competitors(callback.message.chat.id)
    if not comps:
        await callback.answer("Kanal yo'q", show_alert=True)
        return
    rows = [[InlineKeyboardButton(text=f"🗑 @{c['channel_username']}", callback_data=f"comp_del:{c['channel_username']}")] for c in comps]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="comp_back")])
    await callback.message.edit_text("Qaysi kanalni o'chiramiz?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@dp.callback_query(F.data.startswith("comp_del:"))
async def cb_comp_del(callback: CallbackQuery):
    username = callback.data.split(":", 1)[1]
    db.delete_competitor(callback.message.chat.id, username)
    await callback.answer("🗑 O'chirildi")
    await send_competitors_menu(callback.message.chat.id, edit_message=callback.message)


@dp.callback_query(F.data == "comp_back")
async def cb_comp_back(callback: CallbackQuery):
    await send_competitors_menu(callback.message.chat.id, edit_message=callback.message)
    await callback.answer()


# Eski buyruqlar
@dp.message(Command("addcompetitor"))
async def cmd_addcompetitor(message: Message):
    arg = message.text.partition(" ")[2].strip()
    if not arg:
        await message.answer("Foydalanish: /addcompetitor @raqobatchi_kanali")
        return
    username = arg.lstrip("@").strip().lower()
    exists = await channel_exists(username)
    if not exists:
        await message.answer("⚠️ Bu kanal topilmadi yoki yopiq.")
        return
    ok = db.add_competitor(message.chat.id, username)
    await message.answer(f"✅ @{username} kuzatuvga qo'shildi." if ok else "Bu kanal allaqachon ro'yxatda bor.")


@dp.message(Command("mycompetitors"))
async def cmd_mycompetitors(message: Message):
    comps = db.get_competitors(message.chat.id)
    if not comps:
        await message.answer("Hozircha kuzatilayotgan kanal yo'q.")
        return
    lines = [f"• @{c['channel_username']}" for c in comps]
    await message.answer("👀 <b>Kuzatilayotgan kanallar:</b>\n" + "\n".join(lines), parse_mode="HTML")


@dp.message(Command("delcompetitor"))
async def cmd_delcompetitor(message: Message):
    arg = message.text.partition(" ")[2].strip()
    if not arg:
        await message.answer("Foydalanish: /delcompetitor @raqobatchi_kanali")
        return
    ok = db.delete_competitor(message.chat.id, arg)
    await message.answer("🗑 Olib tashlandi." if ok else "Bunday kanal ro'yxatda topilmadi.")


async def fetch_channel_posts(username: str, session: aiohttp.ClientSession):
    url = f"https://t.me/s/{username}"
    try:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()
    except Exception as e:
        log.warning(f"@{username} yuklab bo'lmadi: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for wrap in soup.select("div.tgme_widget_message"):
        data_post = wrap.get("data-post", "")
        if "/" not in data_post:
            continue
        msg_id = int(data_post.split("/")[-1])
        text_div = wrap.select_one(".tgme_widget_message_text")
        text = text_div.get_text("\n").strip() if text_div else "[matnsiz post — rasm/video]"
        posts.append((msg_id, text))
    posts.sort(key=lambda p: p[0])
    return posts


async def channel_exists(username: str) -> bool:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://t.me/s/{username}", timeout=15) as resp:
            return resp.status == 200


async def check_competitors():
    comps = db.get_competitors()
    if not comps:
        return
    by_channel = {}
    for c in comps:
        by_channel.setdefault(c["channel_username"], []).append(c)

    async with aiohttp.ClientSession() as session:
        for username, rows in by_channel.items():
            posts = await fetch_channel_posts(username, session)
            if not posts:
                continue
            for row in rows:
                new_posts = [p for p in posts if p[0] > row["last_message_id"]]
                if not new_posts:
                    continue
                for msg_id, text in new_posts:
                    preview = text[:400] + ("..." if len(text) > 400 else "")
                    link = f"https://t.me/{username}/{msg_id}"
                    notify_text = f"🚨 <b>@{username}</b> yangi post joyladi!\n\n{preview}\n\n🔗 {link}"
                    try:
                        await bot.send_message(row["chat_id"], notify_text, parse_mode="HTML")
                    except Exception as e:
                        log.warning(f"Xabar yuborilmadi {row['chat_id']}: {e}")
                db.update_last_message_id(row["id"], new_posts[-1][0])


# ==================== MAIN ====================

@dp.callback_query(F.data == "back_main")
async def cb_back_main(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


async def main():
    db.init_db()
    scheduler.add_job(check_and_send_reminders, "cron", minute="*")
    scheduler.add_job(check_and_send_scenarios, "cron", minute="*")
    scheduler.add_job(check_competitors, "interval", minutes=COMPETITOR_CHECK_MINUTES)
    scheduler.start()
    log.info("Bot ishga tushdi.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
