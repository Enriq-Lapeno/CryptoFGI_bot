import asyncio
import io
import logging
import os
from collections import defaultdict
from datetime import datetime

import aiohttp
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    BufferedInputFile,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_API_KEY = os.getenv("CMC_API_KEY")

CMC_FNG_API_URL = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/latest"

GAUGE_ZONES = (
    (0, 20, "#c0392b"),
    (20, 40, "#e67e22"),
    (40, 60, "#f1c40f"),
    (60, 80, "#7dcb63"),
    (80, 100, "#27ae60"),
)

CLASSIFICATION_RU = {
    "Extreme Fear": ("Крайний страх", "😱"),
    "Fear": ("Страх", "😨"),
    "Neutral": ("Нейтрально", "😐"),
    "Greed": ("Жадность", "🙂"),
    "Extreme Greed": ("Крайняя жадность", "🤑"),
}

ABOUT_ISZH_TEXT = (
    "📊 <b>Значения индекса страха и жадности:</b>\n\n"
    "от 1 до 10 — Рынок на дне и ищем активно выгодные покупки.\n\n"
    "от 10 до 30 — Рынок ещё падает (short), зарабатываем на падении.\n\n"
    "от 30 до 45 — Выходной, максимально непонятная ситуация на рынке.\n\n"
    "от 45 до 55 — Ситуация более понятная для анализа. Проводим анализ.\n\n"
    "от 55 до 70 — Позитив на рынке! Ищем покупки!!\n\n"
    "от 70 до 90 — Много хороших точек входа для краткосрочной перспективы. "
    "Берём по 1-2% профита с каждого ордера.\n\n"
    "от 90 до 100 — Рынок на пике и ждёт разворота. Либо ждём, пока график "
    "начнёт опускаться, либо сразу ищем шорты."
)

GREETING_TEXT = (
    "Привет! 👋\n"
    "Я показываю Индекс Страха и Жадности крипторынка (Fear & Greed Index) 📊😱🤑\n\n"
    "Нажми кнопку «Узнать какой индекс сейчас» ниже, чтобы получить текущее значение.\n"
    "или «Сбросить всё» чтобы очистить этот чат.🧹"
)


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Узнать какой индекс сейчас")],
            [KeyboardButton(text="Сбросить всё")],
        ],
        resize_keyboard=True,
    )


router = Router()

CHAT_MESSAGE_IDS: dict[int, list[int]] = defaultdict(list)


def track(chat_id: int, message_id: int) -> None:
    CHAT_MESSAGE_IDS[chat_id].append(message_id)


async def fetch_cmc_fear_greed() -> dict:
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            CMC_FNG_API_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()
    return payload["data"]


def render_gauge_png(value: int, label_ru: str) -> bytes:
    fig, ax = plt.subplots(figsize=(6, 4.1), subplot_kw={"aspect": "equal"})
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-0.55, 1.15)
    ax.axis("off")

    for start, end, color in GAUGE_ZONES:
        theta1 = 180 - (end / 100 * 180)
        theta2 = 180 - (start / 100 * 180)
        wedge = plt.matplotlib.patches.Wedge(
            (0, 0), 1.0, theta1, theta2, width=0.28,
            facecolor=color, edgecolor="white", linewidth=1.5,
        )
        ax.add_patch(wedge)

    angle_rad = np.radians(180 - (value / 100 * 180))
    needle_len = 0.85
    ax.plot(
        [0, needle_len * np.cos(angle_rad)], [0, needle_len * np.sin(angle_rad)],
        color="#2c3e50", linewidth=4, solid_capstyle="round", zorder=4,
    )
    ax.add_patch(plt.Circle((0, 0), 0.05, color="#2c3e50", zorder=5))

    ax.text(0, -0.15, f"{value}", ha="center", va="top", fontsize=34, fontweight="bold", color="#2c3e50")
    ax.text(0, -0.42, label_ru, ha="center", va="top", fontsize=15, color="#2c3e50")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", pad_inches=0.15, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def format_cmc_caption(data: dict) -> str:
    value = int(data["value"])
    classification_en = data["value_classification"]
    classification_ru, emoji = CLASSIFICATION_RU.get(classification_en, (classification_en, ""))
    updated_at = datetime.fromisoformat(data["update_time"].replace("Z", "+00:00"))
    updated_at_str = updated_at.strftime("%d.%m.%Y %H:%M UTC")

    return (
        f"{emoji} <b>Индекс страха и жадности (CoinMarketCap)</b>\n\n"
        f"Значение: <b>{value}/100</b> — {classification_ru}\n\n"
        f"Обновлено: {updated_at_str}\n"
        f'Источник: <a href="https://coinmarketcap.com/">CoinMarketCap</a>'
    )


async def send_fgi_report(message: Message) -> None:
    chat_id = message.chat.id
    track(chat_id, message.message_id)

    try:
        cmc_data = await fetch_cmc_fear_greed()
        cmc_value = int(cmc_data["value"])
        cmc_label_ru, _ = CLASSIFICATION_RU.get(cmc_data["value_classification"], (cmc_data["value_classification"], ""))
        gauge_png = render_gauge_png(cmc_value, cmc_label_ru)
    except Exception:
        logging.exception("Failed to fetch/render CoinMarketCap Fear & Greed Index")
        msg = await message.answer("Не удалось получить картинку индекса с CoinMarketCap.")
        track(chat_id, msg.message_id)
        return

    msg2 = await message.answer_photo(
        photo=BufferedInputFile(gauge_png, filename="cmc_fear_and_greed.png"),
        caption=format_cmc_caption(cmc_data),
        parse_mode="HTML",
    )
    track(chat_id, msg2.message_id)

    msg3 = await message.answer(ABOUT_ISZH_TEXT, parse_mode="HTML")
    track(chat_id, msg3.message_id)


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    track(message.chat.id, message.message_id)
    msg = await message.answer(GREETING_TEXT, reply_markup=main_keyboard())
    track(message.chat.id, msg.message_id)


@router.message(Command("fgi"))
async def handle_fgi(message: Message) -> None:
    await send_fgi_report(message)


@router.message(F.text == "Узнать какой индекс сейчас")
async def handle_iszh_button(message: Message) -> None:
    await send_fgi_report(message)


@router.message(F.text == "Сбросить всё")
async def handle_reset(message: Message) -> None:
    chat_id = message.chat.id
    track(chat_id, message.message_id)

    message_ids = CHAT_MESSAGE_IDS.pop(chat_id, [])
    for message_id in message_ids:
        try:
            await message.bot.delete_message(chat_id, message_id)
        except Exception:
            pass

    msg = await message.answer(GREETING_TEXT, reply_markup=main_keyboard())
    track(chat_id, msg.message_id)


@router.message()
async def handle_other(message: Message) -> None:
    track(message.chat.id, message.message_id)


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. Создайте .env на основе .env.example.")

    logging.basicConfig(level=logging.INFO)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
