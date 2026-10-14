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
SCHEDULE_URL_HTTP = "http://sakhgu.ru/studentam/raspisaniye/?group=190"

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

# Кэш на 15 минут
CACHE = {"html": None, "timestamp": 0}
CACHE_TTL = 900


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


async def try_fetch_one(session: aiohttp.ClientSession, name: str, url: str):
    """Пробует скачать страницу через один конкретный шлюз (таймаут 7 сек)"""
    try:
        async with session.get(url, timeout=7, ssl=False) as resp:
            if resp.status == 200:
                text = await resp.text()
                if "ТиГ-09-25" in text or "<table" in text:
                    return True, text
                return False, f"{name}: нет таблицы"
            return False, f"{name}: HTTP {resp.status}"
    except asyncio.TimeoutError:
        return False, f"{name}: таймаут"
    except Exception as e:
        return False, f"{name}: {type(e).__name__}"


async def get_schedule_html():
    """Параллельно опрашивает шлюзы (включая Яндекс и Google) и берет первый успешный ответ"""
    now = time.time()
    if CACHE["html"] and (now - CACHE["timestamp"] < CACHE_TTL):
        return CACHE["html"], None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }

    enc = quote(SCHEDULE_URL, safe="")
    # Набор шлюзов: российские IP Яндекса, Google Proxy, HTTP без SSL и резервные прокси
    gateways = [
        ("Яндекс.Шлюз", f"https://translate.yandex.net/translate?url={enc}&lang=ru-ru"),
        ("Google.Шлюз", "https://sakhgu-ru.translate.goog/studentam/raspisaniye/?group=190&_x_tr_sl=auto&_x_tr_tl=ru&_x_tr_hl=ru"),
        ("Прямой HTTP", SCHEDULE_URL_HTTP),
        ("Слабовид. HTTP", "http://lowvision.sakhgu.ru/studentam/raspisaniye/?group=190"),
        ("ThingProxy", f"https://thingproxy.freeboard.io/fetch/{SCHEDULE_URL}"),
        ("AllOrigins", f"https://api.allorigins.win/raw?url={enc}"),
    ]

    errors = []
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [asyncio.create_task(try_fetch_one(session, name, url)) for name, url in gateways]

        for coro in asyncio.as_completed(tasks):
            ok, result = await coro
            if ok:
                # Как только ХОТЯ БЫ ОДИН шлюз вернул таблицу — отменяем остальные и сразу отдаем результат!
                for t in tasks:
                    if not t.done():
                        t.cancel()
                CACHE["html"] = result
                CACHE["timestamp"] = now
                return result, None
            else:
                errors.append(result)

    return None, ", ".join(errors)


async def fetch_day_schedule(target_day: str) -> str:
    html, err_log = await get_schedule_html()
    if not html:
        return f"⚠️ Сайт СахГУ блокирует запрос из облака.\nДетали: `{err_log}`"

    soup = BeautifulSoup(html, "lxml")

    week_raw = ""
    toolbar_p = soup.select_one("article menu p")
    if toolbar_p:
        week_raw = toolbar_p.get_text(strip=True)

    is_denominator = "знаменател" in week_raw.lower()
    week_dates = re.split(r"\s+[–—-]\s+", week_raw)[0].strip() if week_raw else ""

    target_table = None
    for t in soup.find_all("table"):
        if "ТиГ-09-25" in t.get_text() or "понедельник" in t.get_text().lower():
            target_table = t
            break
    if not target_table:
        target_table = soup.find("table")

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
    # Сразу отвечаем Telegram, чтобы крутилка загрузки сверху не висела!
    await callback.answer()

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
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return

    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(600)
            try:
                async with session.get(url, timeout=10) as resp:
                    pass
            except Exception:
                pass


async def main():
    await start_web_server()
    asyncio.create_task(keep_alive())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
