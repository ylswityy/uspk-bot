import os
import re
import time
import json
import html as html_lib
import asyncio
from datetime import datetime, timedelta, timezone
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

# Кэш живого сайта на 15 минут
CACHE = {"html": None, "timestamp": 0}
CACHE_TTL = 900

# Резервная копия расписания ТиГ-09-25 на случай, если сайт СахГУ лежит (ошибка 502)
# Формат слота: (номер_пары, время, [вариант_числитель, вариант_знаменатель] или [каждую_неделю])
FALLBACK_SCHEDULE = {
    "понедельник": [
        ("3", "12:10-13:40", [
            ("Введение в специальность (Л.)", "Лещенко А.В., преподаватель", "Л.282 / ауд. 312"),
            None
        ]),
        ("4", "13:50-15:20", [
            ("Введение в специальность (Л.)", "Лещенко А.В., преподаватель", "Л.284 / ауд. 100")
        ]),
        ("5", "15:30-17:00", [
            ("Организация экскурсионной деятельности (П.)", "Лысак Е.В., доцент, кандидат наук", "Л.284 / ауд. 206")
        ]),
        ("6", "17:30-19:00", [
            ("Физическая культура (П.)", "Вронский Н.Е., преподаватель", "№ 1 / с/зал")
        ]),
    ],
    "вторник": [
        ("1", "8:30-10:00", [
            ("Сервисная деятельность в туризме и гостеприимстве (П.)", "Павленок А.М., преподаватель", "Л.282 / ауд. 221")
        ]),
        ("2", "10:10-11:40", [
            ("Сервисная деятельность в туризме и гостеприимстве (Л.)", "Павленок А.М., преподаватель", "Л.284 / ауд. 100"),
            ("Основы финансовой грамотности (Л.)", "Павленок А.М., преподаватель", "Л.284 / ауд. 100")
        ]),
        ("3", "12:10-13:40", [
            ("КЛАССНЫЙ ЧАС", "Павленок А.М., преподаватель", "Л.292 / ауд. 118"),
            ("Сервисная деятельность в туризме и гостеприимстве (П.)", "Павленок А.М., преподаватель", "Л.284 / ауд. 205")
        ]),
        ("4", "13:50-15:20", [
            ("Основы финансовой грамотности (П.)", "Павленок А.М., преподаватель", "Л.284 / ауд. 205")
        ]),
    ],
    "среда": [
        ("2", "10:10-11:40", [
            ("Организация гостеприимства (П.)", "Павленок А.М., преподаватель", "Л.284 / ауд. 100"),
            None
        ]),
        ("3", "12:10-13:40", [
            ("Организация гостеприимства (Л.)", "Павленок А.М., преподаватель", "Л.282 / ауд. 312"),
            ("Организация гостеприимства (П.)", "Павленок А.М., преподаватель", "Л.284 / ауд. 206")
        ]),
        ("4", "13:50-15:20", [
            ("Иностранный язык в профессиональной деятельности (П.)", "Богомолова С.С., преподаватель", "Л.284 / ауд. 206")
        ]),
        ("5", "15:30-17:00", [
            ("Введение в специальность (П.)", "Лещенко А.В., преподаватель", "Л.284 / ауд. 205")
        ]),
        ("6", "17:30-19:00", [
            None,
            ("Организация туристской индустрии (Л.)", "Кочанов С.П., старший преподаватель", "Л.284 / ауд. 100")
        ]),
    ],
    "четверг": [
        ("4", "13:50-15:20", [
            ("Организация туристской индустрии (П.)", "Кочанов С.П., старший преподаватель", "Л.282 / ауд. 221")
        ]),
        ("5", "15:30-17:00", [
            ("Организация экскурсионной деятельности (Л.)", "Лысак Е.В., доцент, кандидат наук", "Л.282 / ауд. 312")
        ]),
        ("6", "17:30-19:00", [
            ("Организация экскурсионной деятельности (П.)", "Лысак Е.В., доцент, кандидат наук", "Л.282 / ауд. 312")
        ]),
    ],
    "пятница": [
        ("1", "8:30-10:00", [
            ("История России (П.)", "Хон О.В., преподаватель", "Л.284 / ауд. 206"),
            ("Организация гостеприимства (П.)", "Павленок А.М., преподаватель", "Л.282 / ауд. 312")
        ]),
        ("2", "10:10-11:40", [
            ("Организация гостеприимства (Л.)", "Павленок А.М., преподаватель", "Л.282 / ауд. 312")
        ]),
        ("3", "12:10-13:40", [
            ("Организация туристской индустрии (П.)", "Кочанов С.П., старший преподаватель", "Л.284 / ауд. 206"),
            ("Организация гостеприимства (П.)", "Павленок А.М., преподаватель", "Л.284 / ауд. 206")
        ]),
    ],
    "суббота": [
        ("1", "8:30-10:00", [
            ("Основы бережливого производства (П.)", "И А.Ч., преподаватель", "Л.282 / ауд. 312"),
            ("История России (П.)", "Хон О.В., преподаватель", "Л.282 / ауд. 220")
        ]),
        ("2", "10:10-11:40", [
            ("Основы бережливого производства (Л.)", "И А.Ч., преподаватель", "Л.282 / ауд. 312"),
            ("История России (Л.)", "Хон О.В., преподаватель", "Л.282 / ауд. 312")
        ]),
    ],
}


def get_sakhalin_week_info():
    """Вычисляет даты текущей недели (Пн–Сб) и четность недели по времени Южно-Сахалинска (UTC+11)"""
    sakh_tz = timezone(timedelta(hours=11))
    now = datetime.now(sakh_tz)
    # Если сегодня воскресенье (weekday == 6), показываем расписание на начинающуюся неделю
    days_since_monday = now.weekday()
    if days_since_monday == 6:
        monday = now + timedelta(days=1)
    else:
        monday = now - timedelta(days=days_since_monday)
    saturday = monday + timedelta(days=5)

    week_dates = f"{monday.strftime('%d.%m')}–{saturday.strftime('%d.%m')}"
    # Неделя 12.10–17.10 (ISO неделя 42, четная) в СахГУ идет по знаменателю
    iso_week = monday.isocalendar()[1]
    is_denominator = (iso_week % 2 == 0)
    return week_dates, is_denominator


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
    if subject_cell is None:
        return None
    lines = [line.strip() for line in subject_cell.stripped_strings if line.strip()]
    room = " ".join(room_cell.stripped_strings) if room_cell else ""
    if not lines:
        return None
    subject = lines[0]
    teacher = ", ".join(lines[1:]) if len(lines) > 1 else "Не указан"
    return {
        "subject": subject,
        "teacher": teacher,
        "room": room if room else "Не указана"
    }


async def try_fetch_one(session: aiohttp.ClientSession, name: str, url: str, extra_headers: dict = None, is_json: bool = False):
    try:
        async with session.get(url, headers=extra_headers, timeout=6, ssl=False) as resp:
            if resp.status == 200:
                raw_bytes = await resp.read()
                text = raw_bytes.decode("utf-8", errors="ignore")
                if is_json:
                    data = json.loads(text)
                    text = data.get("contents", "") or ""
                if "ТиГ-09-25" in text or ("понедельник" in text.lower() and "лещенко" in text.lower()):
                    print(f"[OK] Сайт получен через {name}", flush=True)
                    return True, text
                return False, f"{name}: нет таблицы"
            return False, f"{name}: HTTP {resp.status}"
    except Exception as e:
        return False, f"{name}: {type(e).__name__}"


async def get_schedule_html():
    now = time.time()
    if CACHE["html"] and (now - CACHE["timestamp"] < CACHE_TTL):
        return CACHE["html"]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }

    enc = quote(SCHEDULE_URL, safe="")
    gateways = [
        ("Google.Translate", "https://sakhgu-ru.translate.goog/studentam/raspisaniye/?group=190&_x_tr_sl=en&_x_tr_tl=ru&_x_tr_hl=ru", None, False),
        ("AllOrigins.JSON", f"https://api.allorigins.win/get?url={enc}", None, True),
        ("CodeTabs", f"https://api.codetabs.com/v1/proxy/?quest={SCHEDULE_URL}", None, False),
        ("Jina.HTML", f"https://r.jina.ai/{SCHEDULE_URL}", {"X-Return-Format": "html"}, False),
        ("Прямой HTTPS", SCHEDULE_URL, None, False),
        ("Слабовид. HTTPS", "https://lowvision.sakhgu.ru/studentam/raspisaniye/?group=190", None, False),
    ]

    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [
            asyncio.create_task(try_fetch_one(session, name, url, extra_h, is_json))
            for name, url, extra_h, is_json in gateways
        ]

        for coro in asyncio.as_completed(tasks):
            ok, result = await coro
            if ok:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                CACHE["html"] = result
                CACHE["timestamp"] = now
                return result

    # Если сайт СахГУ лежит, но есть старый кэш — отдаем его
    if CACHE["html"]:
        return CACHE["html"]
    return None


def format_fallback_schedule(target_day: str) -> str:
    """Формирует расписание из встроенной базы, если сайт СахГУ временно отдает 502"""
    week_dates, is_denominator = get_sakhalin_week_info()
    header = f"📅 <b>{DAYS[target_day]} ({week_dates})</b>\n"

    day_slots = FALLBACK_SCHEDULE.get(target_day.lower(), [])
    blocks = []

    for pair_num, time_str, variants in day_slots:
        chosen = variants[0] if len(variants) == 1 else (variants[1] if is_denominator else variants[0])
        if not chosen:
            continue

        subj, teacher, room = chosen
        blocks.append(
            f"🔔 <b>{html_lib.escape(pair_num)} пара</b> | ⏰ <code>{html_lib.escape(time_str)}</code>\n"
            f"📚 <b>{html_lib.escape(subj)}</b>\n"
            f"👤 {html_lib.escape(teacher)}\n"
            f"🚪 {html_lib.escape(room)}"
        )

    if not blocks:
        return header + "\n🎉 В этот день пар нет!"

    return header + "\n" + "\n\n".join(blocks)


async def fetch_day_schedule(target_day: str) -> str:
    html = await get_schedule_html()
    if not html:
        return format_fallback_schedule(target_day)

    soup = BeautifulSoup(html, "lxml")

    week_dates, is_denominator = get_sakhalin_week_info()
    toolbar_p = soup.select_one("article menu p")
    if toolbar_p:
        week_raw = toolbar_p.get_text(strip=True)
        if "знаменател" in week_raw.lower() or "числител" in week_raw.lower():
            is_denominator = "знаменател" in week_raw.lower()
            parsed_dates = re.split(r"\s+[–—-]\s+по", week_raw)[0].strip()
            if parsed_dates:
                week_dates = parsed_dates

    target_table = None
    for t in soup.find_all("table"):
        t_text = t.get_text().lower()
        if "тиг-09-25" in t_text or ("понедельник" in t_text and "вторник" in t_text):
            target_table = t
            break

    if not target_table:
        return format_fallback_schedule(target_day)

    grid = table_to_grid(target_table)

    slots = []
    slot_map = {}

    for row in grid:
        if len(row) < 4:
            continue

        day_text = row[0].get_text(strip=True).lower()
        if target_day.lower() not in day_text:
            continue

        time_str = row[1].get_text(strip=True)
        pair_num = row[2].get_text(strip=True)

        if not time_str or not pair_num or not any(ch.isdigit() for ch in pair_num):
            continue

        key = (pair_num, time_str)
        if key not in slot_map:
            slot_entry = {"num": pair_num, "time": time_str, "rows": []}
            slot_map[key] = slot_entry
            slots.append(slot_entry)

        subj_cell = row[3] if len(row) > 3 else None
        room_cell = row[4] if len(row) > 4 else None
        slot_map[key]["rows"].append((subj_cell, room_cell))

    if not slots:
        return format_fallback_schedule(target_day)

    header = f"📅 <b>{DAYS[target_day]} ({html_lib.escape(week_dates)})</b>\n"

    blocks = []
    for slot in slots:
        rows = slot["rows"]
        chosen_row = rows[0] if len(rows) == 1 else (rows[1] if is_denominator else rows[0])

        info = parse_lesson_cell(chosen_row[0], chosen_row[1])
        if not info:
            continue

        blocks.append(
            f"🔔 <b>{html_lib.escape(slot['num'])} пара</b> | ⏰ <code>{html_lib.escape(slot['time'])}</code>\n"
            f"📚 <b>{html_lib.escape(info['subject'])}</b>\n"
            f"👤 {html_lib.escape(info['teacher'])}\n"
            f"🚪 {html_lib.escape(info['room'])}"
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
    await callback.answer()

    try:
        schedule_text = await fetch_day_schedule(target_day)
    except Exception as e:
        schedule_text = format_fallback_schedule(target_day)

    try:
        await callback.message.edit_text(
            text=schedule_text,
            parse_mode="HTML",
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
