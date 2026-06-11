"""/referral — referral program handler.

Shows user's referral link + stats, with share button.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.services import referral
from bot.services.scenes import get_scene

logger = logging.getLogger(__name__)

router = Router(name="referral")


@router.message(Command("referral"))
@router.message(Command("ref"))
async def cmd_referral(message: Message) -> None:
    """Show user's referral link + stats."""
    if message.from_user is None:
        return
    tg_id = message.from_user.id
    stats = await referral.get_referral_stats(tg_id)
    if stats is None:
        await message.answer(
            "✗ Не удалось получить реферальную ссылку. Попробуй позже."
        )
        return

    # Send referral scene
    try:
        png = get_scene("referral")
        await message.answer_photo(
            photo=BufferedInputFile(png, filename="referral.png"),
            caption="",
        )
    except Exception:  # noqa: BLE001
        pass

    text = (
        "✦ <b>Реферальная программа Buildo</b>\n\n"
        f"<b>Твоя ссылка:</b>\n<code>{stats.bot_url}</code>\n\n"
        f"<b>Статистика:</b>\n"
        f"• L1 (прямые): {stats.by_level.get(1, 0)} чел.\n"
        f"• L2 (через них): {stats.by_level.get(2, 0)} чел.\n"
        f"• L3 (3-й уровень): {stats.by_level.get(3, 0)} чел.\n"
        f"• Всего приглашено: {stats.total_referrals} чел.\n"
        f"• <b>Заработано: {stats.total_earnings_rub:.0f}₽</b>\n\n"
        "<b>Как это работает:</b>\n"
        "• L1 — 30% с каждой оплаты приглашённого\n"
        "• L2 — 10% с оплат рефералов твоих рефералов\n"
        "• L3 — 5% с оплат на 3-м уровне\n\n"
        "💸 Вывод средств — через поддержку (фаза 1.5: автовыплаты)"
    )

    share_text = (
        "✦ Создай сайт через AI за минуту — https://t.me/buildo_aibot\n"
        f"Используй мою ссылку и получи бонус: {stats.bot_url}"
    )
    share_url = (
        f"https://t.me/share/url?url={stats.bot_url}&text="
        + share_text.replace(" ", "%20").replace("\n", "%0A")
    )

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📤 Поделиться ссылкой", url=share_url)],
                [InlineKeyboardButton(text="📋 В меню", callback_data="menu:home")],
            ]
        ),
    )
