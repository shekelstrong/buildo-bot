"""Global command-escape router.

Любая "выходная" команда (start, cancel, menu) должна работать из ЛЮБОГО FSM state.
Регистрируется ПЕРВЫМ в main.py чтобы перехватывать сообщения до state-bound handlers.

Решает проблему: юзер в SiteFlow.editing пишет /start — бот интерпретирует как
инструкцию для правки, а не как escape в меню.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

logger = logging.getLogger(__name__)

router = Router(name="commands-escape")


@router.message(CommandStart())
@router.message(Command("cancel"))
@router.message(Command("menu"))
@router.message(F.text.casefold().in_({"отмена", "в меню", "стоп", "стоп."}))
async def global_escape(message: Message, state: FSMContext) -> None:
    """Любая "стоп" команда → сброс state + сообщение."""
    await state.clear()
    await message.answer(
        "✦ <b>Сбросил состояние</b>\n\n"
        "Возвращайся в главное меню: жми /start или используй кнопки."
    )
