"""
Yagona fayl — ikkala vazifani ham bajaradi:
1) Kunlik vazifa eslatmalari
2) Raqobatchi Telegram kanallarini kuzatish

Raqobatchi kanalni kuzatish uchun Telegram'ning ochiq veb-ko'rinishi
(https://t.me/s/kanal_nomi) ishlatiladi — bu hech qanday login yoki
qo'shimcha API kalit talab qilmaydi, faqat BOT_TOKEN kifoya.

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
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
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
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=TIMEZONE)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
TZ = ZoneInfo(TIMEZONE)


def now_hhmm() -> str:
    """Belgilangan vaqt zonasi (masalan Asia/Tashkent) bo'yicha joriy vaqtni HH:MM formatida qaytaradi."""
    return datetime.now(TZ).strftime("%H:%M")


def today_local() -> date:
    return datetime.now(TZ).date()

HELP_TEXT = (
    "🤖 <b>Buyruqlar</b>\n\n"
    "<b>Kunlik vazifalar:</b>\n"
    "/addtask &lt;matn&gt; — yangi vazifa qo'shish\n"
    "/mytasks — vazifalar ro'yxati\n"
    "/deltask &lt;raqam&gt; — vazifani o'chirish\n"
    "/cleartasks — barcha vazifalarni tozalash\n"
    "/settime SS:DD — eslatma vaqtini o'rnatish (masalan 09:00)\n"
    "/remindon — kunlik eslatmani yoqish\n"
    "/remindoff — kunlik eslatmani o'chirish\n\n"
    "<b>Raqobatchilarni kuzatish:</b>\n"
    "/addcompetitor @kanal_nomi — kuzatuvga qo'shish\n"
    "/mycompetitors — kuzatilayotgan kanallar ro'yxati\n"
    "/delcompetitor @kanal_nomi — kuzatuvdan olib tashlash\n\n"
    f"Kanal yangi post chiqarsa, {COMPETITOR_CHECK_MINUTES} daqiqa ichida avtomatik xabar keladi.\n\n"
    "<b>Kunlik reels ssenariylari:</b>\n"
    "/scenarios — hozir tasodifiy ssenariylar olish\n"
    "/scenariotime SS:DD — har kuni qaysi vaqtda kelishini belgilash\n"
    "/scenariocount N — kuniga nechta ssenariy kerakligini belgilash (default 10)\n"
    "/scenarioson /scenariosoff — kunlik yuborishni yoqish/o'chirish\n"
    "/startchallenge — 100 kunlik challenge kun hisoblagichini boshlash\n"
    "/challengeday — nechanchi kunda ekaningizni ko'rish"
)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    db.add_user(message.chat.id, message.from_user.username)
    await message.answer(
        "Salom! 👋 Men sizga ikki narsada yordam beraman:\n\n"
        "1️⃣ Har kuni bajarilishi kerak bo'lgan vazifalarni eslatib turaman\n"
        "2️⃣ Raqobatchilaringizning Telegram kanallarini kuzatib, "
        "ular yangi post qilganda xabar beraman\n\n" + HELP_TEXT,
        parse_mode="HTML",
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, parse_mode="HTML")


# ---------------- TASKS ----------------

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
        await message.answer("Foydalanish: /deltask 3  (raqamni /mytasks orqali ko'ring)")
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
        await message.answer("Foydalanish: /settime 09:00  (24 soatlik format)")
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


# ---------------- COMPETITORS ----------------

@dp.message(Command("addcompetitor"))
async def cmd_addcompetitor(message: Message):
    arg = message.text.partition(" ")[2].strip()
    if not arg:
        await message.answer("Foydalanish: /addcompetitor @raqobatchi_kanali")
        return
    username = arg.lstrip("@").strip().lower()
    exists = await channel_exists(username)
    if not exists:
        await message.answer(
            "⚠️ Bu kanal topilmadi yoki yopiq (public emas). "
            "Faqat ochiq (@username bilan) kanallarni kuzatish mumkin."
        )
        return
    ok = db.add_competitor(message.chat.id, username)
    if ok:
        await message.answer(f"✅ @{username} kuzatuvga qo'shildi.")
    else:
        await message.answer("Bu kanal allaqachon ro'yxatda bor.")


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


# ---------------- CONTENT SCENARIOS ----------------

def pick_scenarios(chat_id: int, count: int) -> list[dict]:
    """Takrorlanmasdan tasodifiy ssenariy tanlaydi; hammasi tugasa, ro'yxat qayta boshlanadi."""
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


@dp.message(Command("scenarios"))
async def cmd_scenarios(message: Message):
    user = db.get_user(message.chat.id)
    count = user["scenario_count"] if user else 10
    scenarios = pick_scenarios(message.chat.id, count)
    await message.answer(format_scenarios(message.chat.id, scenarios), parse_mode="HTML")


@dp.message(Command("scenariotime"))
async def cmd_scenariotime(message: Message):
    arg = message.text.partition(" ")[2].strip()
    if not TIME_RE.match(arg):
        await message.answer("Foydalanish: /scenariotime 06:00  (24 soatlik format)")
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


@dp.message(Command("startchallenge"))
async def cmd_startchallenge(message: Message):
    db.start_challenge(message.chat.id, today_local().isoformat())
    await message.answer(
        "🏁 100 kunlik challenge boshlandi! Kun 1/100.\n"
        "Kunlik ssenariylarni yoqish uchun: /scenariotime 06:00"
    )


@dp.message(Command("challengeday"))
async def cmd_challengeday(message: Message):
    user = db.get_user(message.chat.id)
    if not user or not user["challenge_start_date"]:
        await message.answer("Challenge hali boshlanmagan. /startchallenge bilan boshlang.")
        return
    start = date.fromisoformat(user["challenge_start_date"])
    day_num = (today_local() - start).days + 1
    await message.answer(f"📅 Siz hozir {day_num}/100-kundasiz.")


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


# ---------------- COMPETITOR MONITORING (t.me/s/ scraping) ----------------

async def fetch_channel_posts(username: str, session: aiohttp.ClientSession):
    """https://t.me/s/username dan oxirgi postlarni oladi (login shart emas)."""
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


# ---------------- SCHEDULER (daily reminders) ----------------

async def check_and_send_reminders():
    now_str = now_hhmm()
    for user in db.get_all_users():
        if not user["reminders_on"]:
            continue
        if user["reminder_time"] != now_str:
            continue
        tasks = db.get_tasks(user["chat_id"])
        if not tasks:
            text = "☀️ Xayrli tong! Bugunga hali vazifa qo'shmagansiz — /addtask bilan qo'shing."
        else:
            lines = [f"☐ {t['text']}" for t in tasks]
            text = "☀️ <b>Bugungi vazifalaringiz:</b>\n" + "\n".join(lines)
        try:
            await bot.send_message(user["chat_id"], text, parse_mode="HTML")
        except Exception as e:
            log.warning(f"Reminder yuborilmadi {user['chat_id']}: {e}")


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
