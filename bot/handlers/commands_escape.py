"""Global command-escape router (только для /cancel, /menu, текстовых триггеров).

НЕ перехватывает /start — для /start есть отдельная защита (state.clear() в начале
state-bound handlers в site_builder).

Регистрируется ПЕРВЫМ в main.py чтобы /cancel и текст 'в меню'/'отмена'/'стоп'
сбрасывали state ДО того как site_builder успеет поймать их в editing.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

logger = logging.getLogger(__name__)

router = Router(name="commands-escape")


@router.message(Command("cancel"))
@router.message(Command("menu"))
@router.message(F.text.casefold().in_({"отмена", "в меню", "стоп", "стоп."}))
async def global_escape(message: Message, state: FSMContext) -> None:
    """Сброс state + сообщение. /start НЕ здесь — он идёт в cmd_start."""
    await state.clear()
    await message.answer(
        "✦ <b>Главное меню</b>\n\n" "Жми кнопки ниже или напиши что хочешь сделать.",
        reply_markup=_main_menu_kb(),
    )


def _main_menu_kb():
    """Inline клавиатура главного меню (копия common._main_keyboard)."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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
            [
                InlineKeyboardButton(text="🐙 GitHub", callback_data="menu:github"),
                InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu:help"),
            ],
        ]
    )
