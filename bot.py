import os
import re
import time
import asyncio
from urllib.parse import quote
import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.getenv("BOT_TOKEN", "8739152380:AAFPwserjejoMl8mW-n05rCNES4IJlwZgCM")
SCHEDULE_URL = "https://sakhgu.ru/studentam/raspisaniye/?group=190"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DAYS = {
    "понедельник": "Понедельник",
    "вторник": "Вторник",
    "среда": "Среда",
    "четверг": "Четверг",
    "пятница": "Пятница",
    "суббота": "Суббота"
}

# Кэш на 10 минут, чтобы кнопки переключались мгновенно
CACHE = {"html": None, "timestamp": 0}
CACHE_TTL = 600


def get_days_keyboard(selected_day: str = None):
    builder = InlineKeyboardBuilder()
    for callback_day, title in DAYS.items():
        btn_text = f"• {title} •" if callback_day == selected_day else title
        builder.button(text=btn_text, callback_data=f"day:{callback_day}")
    builder.adjust(2)
    return builder.as_markup()


def table_to_grid(table_tag):
    rows = table_tag.find_all("tr")
    grid = []
    span_map = {}

    for r_idx, row in enumerate(rows):
        grid_row = []
        c_idx = 0
        cells = row.find_all(["td", "th"])
        cell_iter = iter(cells)

        while True:
            if (r_idx, c_idx) in span_map:
                grid_row.append(span_map.pop((r_idx, c_idx)))
                c_idx += 1
            else:
                try:
                    cell = next(cell_iter)
                except StopIteration:
                    break

                grid_row.append(cell)
                rowspan = int(cell.get("rowspan", 1))
                colspan = int(cell.get("colspan", 1))

                for dr in range(rowspan):
                    for dc in range(colspan):
                        if dr == 0 and dc == 0:
                            continue
                        span_map[(r_idx + dr, c_idx + dc)] = cell
                c_idx += 1

        while (r_idx, c_idx) in span_map:
            grid_row.append(span_map.pop((r_idx, c_idx)))
            c_idx += 1

        if grid_row:
            grid.append(grid_row)
    return grid


def parse_lesson_cell(subject_cell, room_cell):
    lines = [line.strip() for line in subject_cell.stripped_strings if line.strip()]
    room = " ".join(room_cell.stripped_strings)
    if not lines:
        return None
    subject = lines[0]
    teacher = ", ".join(lines[1:]) if len(lines) > 1 else "Не указан"
    return {
        "subject": subject,
        "teacher": teacher,
        "room": room if room else "Не указана"
    }


async def get_schedule_html() -> str:
    """Скачивает HTML с обходом ошибки 502 и кэшированием на 10 минут"""
    now = time.time()
    if CACHE["html"] and (now - CACHE["timestamp"] < CACHE_TTL):
        return CACHE["html"]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    encoded_url = quote(SCHEDULE_URL, safe="")
    urls_to_try = [
        SCHEDULE_URL,
        "https://lowvision.sakhgu.ru/studentam/raspisaniye/?group=190",
        f"https://api.allorigins.win/raw?url={encoded_url}",
        f"https://api.codetabs.com/v1/proxy?quest={SCHEDULE_URL}",
        f"https://corsproxy.io/?{encoded_url}",
    ]

    async with aiohttp.ClientSession(headers=headers) as session:
        for url in urls_to_try:
            try:
                async with session.get(url, timeout=15, ssl=False) as response:
                    if response.status == 200:
                        text = await response.text()
                        if "<table" in text:
                            CACHE["html"] = text
                            CACHE["timestamp"] = now
                            return text
            except Exception:
                continue

    return None


async def fetch_day_schedule(target_day: str) -> str:
    html = await get_schedule_html()
    if not html:
        return "⚠️ Не удалось достучаться до сайта СахГУ (ошибка 502). Попробуй нажать еще раз через минуту!"

    soup = BeautifulSoup(html, "lxml")

    week_raw = ""
    toolbar_p = soup.select_one("article menu p")
    if toolbar_p:
        week_raw = toolbar_p.get_text(strip=True)

    is_denominator = "знаменател" in week_raw.lower()
    week_dates = re.split(r"\s+[–—-]\s+", week_raw)[0].strip() if week_raw else ""

    target_table = soup.select_one("article table") or soup.find("table")
    if not target_table:
        return "⚠️ Таблица с расписанием не найдена на странице."

    grid = table_to_grid(target_table)

    slots = []
    slot_map = {}

    for row in grid:
        if len(row) < 5:
            continue

        day_text = row[0].get_text(strip=True).lower()
        if day_text != target_day.lower():
            continue

        time_str = row[1].get_text(strip=True)
        pair_num = row[2].get_text(strip=True)

        if not time_str or not pair_num:
            continue

        key = (pair_num, time_str)
        if key not in slot_map:
            slot_entry = {"num": pair_num, "time": time_str, "rows": []}
            slot_map[key] = slot_entry
            slots.append(slot_entry)

        slot_map[key]["rows"].append((row[3], row[4]))

    header = f"📅 **{DAYS[target_day]} ({week_dates})**\n" if week_dates else f"📅 **{DAYS[target_day]}**\n"

    blocks = []
    for slot in slots:
        rows = slot["rows"]
        chosen_row = rows[0] if len(rows) == 1 else (rows[1] if is_denominator else rows[0])

        info = parse_lesson_cell(chosen_row[0], chosen_row[1])
        if not info:
            continue

        blocks.append(
            f"🔔 **{slot['num']} пара** | ⏰ `{slot['time']}`\n"
            f"📚 **{info['subject']}**\n"
            f"👤 {info['teacher']}\n"
            f"🚪 {info['room']}"
        )

    if not blocks:
        return header + "\n🎉 В этот день пар нет!"

    return header + "\n" + "\n\n".join(blocks)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Выбери день недели, чтобы посмотреть расписание:",
        reply_markup=get_days_keyboard()
    )


@dp.callback_query(F.data.startswith("day:"))
async def process_day_button(callback: CallbackQuery):
    target_day = callback.data.split(":")[1]
    await callback.answer("Загружаю...")

    try:
        schedule_text = await fetch_day_schedule(target_day)
    except Exception as e:
        schedule_text = f"⚠️ Ошибка при обработке расписания: `{e}`"

    try:
        await callback.message.edit_text(
            text=schedule_text,
            parse_mode="Markdown",
            reply_markup=get_days_keyboard(selected_day=target_day)
        )
    except Exception:
        pass


# --- Мини веб-сервер + Авто-пингователь против засыпания через 15 минут ---
async def health_check(request):
    return web.Response(text="Bot is alive!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def keep_alive():
    """Пингует внешний адрес Render каждые 10 минут, чтобы сервер не уходил в сон"""
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return  # Если запущено локально, ничего не делаем

    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(600)  # Ждем 10 минут (600 секунд)
            try:
                async with session.get(url, timeout=10) as resp:
                    pass
            except Exception:
                pass


async def main():
    await start_web_server()
    asyncio.create_task(keep_alive())  # Запускаем анти-сон в фоне
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
