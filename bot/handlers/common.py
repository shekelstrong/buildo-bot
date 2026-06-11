"""Common handlers — /start, /help, fallback."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Greet user and show main actions inline (not as numbered buttons)."""
    if message.from_user is None:
        return
    name = message.from_user.first_name or "друг"
    await message.answer(
        f"Привет, {name}! 👋\n\n"
        "Я <b>Buildo</b> — AI-платформа для создания сайтов.\n\n"
        "Что я умею:\n"
        "• 🚀 Создать сайт по твоему описанию\n"
        "• 📦 Задеплоить его в один клик (Layero / Beget / GitHub / GitVerse)\n"
        "• 🌐 Подключить свой домен\n"
        "• ✏️ Внести правки через чат\n"
        "• 📝 Вести SEO-блог с AI-агентом\n\n"
        "Напиши что хочешь сделать, например:\n"
        "<i>«Сделай лендинг для кофейни в стиле минимализм»</i>\n\n"
        "Или используй команды:\n"
        "/site — создать сайт\n"
        "/sites — мои сайты\n"
        "/articles — SEO-статьи\n"
        "/help — справка"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "📚 <b>Справка Buildo</b>\n\n"
        "<b>Основные команды:</b>\n"
        "/start — главное меню\n"
        "/site — создать новый сайт\n"
        "/sites — список моих сайтов\n"
        "/articles — SEO-статьи и блог\n"
        "/help — эта справка\n\n"
        "<b>Как создать сайт:</b>\n"
        "1. Напиши «сделай лендинг для ...» или используй /site\n"
        "2. Я сгенерирую код и покажу превью\n"
        "3. Скажи «задеплой» — отправлю на хостинг\n"
        "4. Опционально подключи свой домен\n\n"
        "<b>Хостинг:</b>\n"
        "• Layero (по умолчанию, бесплатно)\n"
        "• Beget Cloud VPS (для продвинутых)\n"
        "• GitHub / GitVerse (только код)\n\n"
        "Вопросы — пиши прямо сюда в чат."
    )


@router.message(Command("cancel"))
@router.message(F.text.casefold() == "отмена")
async def cmd_cancel(message: Message, state) -> None:  # type: ignore[no-untyped-def]
    """Reset any active FSM state."""
    await state.clear()
    await message.answer("Окей, отменил. Что дальше?")


@router.message()
async def fallback(message: Message) -> None:
    """Catch-all for unrecognized messages — guide user to commands."""
    await message.answer(
        "Я тебя понял, но это пока вне моего скоупа. 🤖\n\n"
        "Попробуй:\n"
        "• <b>/start</b> — главное меню\n"
        "• <b>/site</b> — создать сайт\n"
        "• Опиши задачу словами, например: <i>«сделай лендинг для кофейни»</i>"
    )
