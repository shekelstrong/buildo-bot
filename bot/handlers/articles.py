"""Articles — SEO blog posts (Phase 1.5 placeholder).

In Phase 1 this is a stub. Phase 1.5 will add:
- AI generation of 2000+ word articles
- SEO/GEO/AEO optimization
- Auto-publishing via cron
- Integration with user's generated sites
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

logger = logging.getLogger(__name__)

router = Router(name="articles")


@router.message(Command("articles"))
async def cmd_articles(message: Message) -> None:
    """List user's articles (Phase 1.5 stub)."""
    await message.answer(
        "✦ <b>SEO-статьи и блог</b>\n\n"
        "<i>Фаза 1.5 — скоро!</i>\n\n"
        "Вот что будет:\n"
        "• AI-генерация статей 2000+ слов на любую тему\n"
        "• SEO/GEO/AEO оптимизация под поисковики и AI-ассистенты\n"
        "• Автопубликация по расписанию (cron)\n"
        "• Связка с твоим сайтом: статьи автоматически идут в /articles секцию\n"
        "• Внутренняя перелинковка между статьями\n\n"
        "Пока можно:\n"
        "• Заказать статью вручную через /admin (фаза 1)\n"
        "• Подождать релиза 1.5 (target: июль 2026)",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📋 В меню", callback_data="menu:home")]
            ]
        ),
    )
