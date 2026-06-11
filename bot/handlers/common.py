"""Common handlers — /start, /help, /admin, fallback.

All replies send a scene image first (where appropriate), then a styled
text message with premium typography and inline buttons.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.services.scenes import get_scene

logger = logging.getLogger(__name__)

router = Router(name="common")


def _main_keyboard() -> InlineKeyboardMarkup:
    """Main menu inline keyboard — always visible from /start."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✦ Создать сайт", callback_data="menu:site"),
                InlineKeyboardButton(text="📦 Мои сайты", callback_data="menu:sites"),
            ],
            [
                InlineKeyboardButton(
                    text="📝 SEO-статьи", callback_data="menu:articles"
                ),
                InlineKeyboardButton(text="👥 Рефералы", callback_data="menu:referral"),
            ],
            [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu:help")],
        ]
    )


async def _send_scene(message: Message, scene_name: str) -> None:
    """Send a scene PNG as a photo (Telegram renders inline)."""
    try:
        png = get_scene(scene_name)
        await message.answer_photo(
            photo=BufferedInputFile(png, filename=f"{scene_name}.png"),
            caption="",  # caption goes in the next message
        )
    except Exception:  # noqa: BLE001
        logger.exception("scene %s send failed", scene_name)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Greet user with welcome scene + main menu."""
    if message.from_user is None:
        return
    name = message.from_user.first_name or "друг"
    await _send_scene(message, "welcome")
    await message.answer(
        f"✦ <b>Привет, {name}!</b>\n\n"
        "Я <b>Buildo</b> — AI-платформа для создания сайтов.\n"
        "Опиши словами, что нужно — я сгенерирую код и задеплою за тебя.\n\n"
        "<b>Что умею:</b>\n"
        "• ✦ Создать сайт по твоему описанию\n"
        "• ✦ Задеплоить его автоматически (Layero / GitHub)\n"
        "• ✦ Внести правки через чат (диалог-агент)\n"
        "• ✦ Вести SEO-блог с AI\n"
        "• ✦ Привести друзей и заработать\n\n"
        "Или используй кнопки ниже:",
        reply_markup=_main_keyboard(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "✦ <b>Справка Buildo</b>\n\n"
        "<b>Основные команды:</b>\n"
        "/start — главное меню\n"
        "/site — создать новый сайт\n"
        "/sites — список моих сайтов\n"
        "/articles — SEO-статьи и блог\n"
        "/help — эта справка\n"
        "/admin — управление (для владельца)\n\n"
        "<b>Как создать сайт:</b>\n"
        "1. Жми «✦ Создать сайт» или пиши /site\n"
        "2. Опиши словами, что нужно\n"
        "3. Я сгенерирую код и покажу превью\n"
        "4. Скажи «задеплой» или жми «✅ Готово»\n"
        "5. Опционально подключи свой домен (фаза 1.5)\n\n"
        "<b>Хостинг:</b>\n"
        "• Layero (по умолчанию, бесплатно, РФ)\n"
        "• GitHub Pages (бэкап + история)\n\n"
        "Вопросы — пиши прямо сюда в чат."
    )


@router.message(Command("cancel"))
@router.message(F.text.casefold() == "отмена")
@router.message(F.text.casefold() == "в меню")
async def cmd_cancel(message: Message, state) -> None:  # type: ignore[no-untyped-def]
    """Reset any active FSM state. 'В меню' as a phrase also works."""
    await state.clear()
    await message.answer(
        "✦ <b>Главное меню</b>\n\n" "Выбери действие или напиши что хочешь сделать.",
        reply_markup=_main_keyboard(),
    )


@router.message()
async def fallback(message: Message) -> None:
    """Catch-all for unrecognized messages — guide user to commands."""
    if message.text and message.text.startswith("/"):
        return  # Don't intercept unknown commands
    await message.answer(
        "✦ Это пока вне моего скоупа.\n\n"
        "Попробуй:\n"
        "• <b>/site</b> — создать сайт\n"
        "• Опиши задачу словами: <i>«сделай лендинг для кофейни»</i>\n"
        "• Или используй кнопки из /start"
    )
