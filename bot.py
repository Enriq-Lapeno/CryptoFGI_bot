import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    BufferedInputFile,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_API_KEY = os.getenv("CMC_API_KEY")

CMC_FNG_API_URL = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/latest"
CMC_FNG_PAGE_URL = "https://coinmarketcap.com/ru/charts/fear-and-greed-index/"
CMC_FNG_WIDGET_TITLE = "Индекс страха и жадности CMC"

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
    "или «Очистить чат» чтобы очистить этот чат.🧹"
)


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Узнать какой индекс сейчас")],
            [KeyboardButton(text="Очистить чат")],
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


async def capture_cmc_widget_png() -> bytes:
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.goto(CMC_FNG_PAGE_URL, wait_until="networkidle", timeout=30000)

            try:
                await page.click("#onetrust-accept-btn-handler", timeout=3000)
            except Exception:
                pass

            handle = await page.evaluate_handle(
                """(title) => {
                    const h2 = Array.from(document.querySelectorAll('h2'))
                        .find(el => el.textContent.trim() === title);
                    if (!h2) return null;
                    let el = h2;
                    for (let i = 0; i < 6 && el; i++) {
                        if (el.querySelector('svg')) return el;
                        el = el.parentElement;
                    }
                    return null;
                }""",
                CMC_FNG_WIDGET_TITLE,
            )
            element = handle.as_element()
            if element is None:
                raise RuntimeError("Виджет индекса не найден на странице CoinMarketCap")

            return await element.screenshot(type="png")
        finally:
            await browser.close()


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
        screenshot_png = await capture_cmc_widget_png()
    except Exception:
        logging.exception("Failed to fetch/screenshot CoinMarketCap Fear & Greed Index")
        msg = await message.answer("Не удалось получить картинку индекса с CoinMarketCap.")
        track(chat_id, msg.message_id)
        return

    msg2 = await message.answer_photo(
        photo=BufferedInputFile(screenshot_png, filename="cmc_fear_and_greed.png"),
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


@router.message(F.text == "Очистить чат")
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
