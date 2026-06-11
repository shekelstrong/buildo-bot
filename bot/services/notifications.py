"""Buildo notifications — admin + referrer alerts.

Sends messages to admin (with full info) and to referrers (anonymous info).
Privacy rule:
  - Referrer: NO tg_user_id, NO username, NO link. Only event type.
  - Admin:    full info including tg_user_id, username, link.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot

from bot.config import get_settings
from bot.services.referral import ReferrerInfo

logger = logging.getLogger(__name__)


async def _send(bot: Bot, chat_id: int, text: str) -> bool:
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify.send failed chat=%s: %s", chat_id, exc)
        return False


async def notify_signup(
    bot: Bot,
    referrers: list[ReferrerInfo],
    new_user_tg_id: int,
    new_user_username: str | None,
    new_user_first_name: str | None,
    ref_code: str,
) -> None:
    """Notify referrers (anonymous) + admin (full info) of a new signup."""
    s = get_settings()
    if not s.admin_telegram_id:
        logger.warning("admin_telegram_id not set, skipping admin notify")
        return

    # 1) Referrers — anonymous
    for ref in referrers:
        if ref.tg_user_id is None:
            continue
        if ref.level == 1:
            text = (
                "✦ <b>Новый пользователь по вашей ссылке</b>\n\n"
                f"Кто-то присоединился к Buildo по вашей реферальной ссылке.\n"
                f"Когда он оплатит подписку — вы получите 30% от суммы."
            )
        else:
            text = (
                f"✦ <b>Новый пользователь по вашей L{ref.level} ссылке</b>\n\n"
                f"Кто-то присоединился к Buildo через вашу реферальную сеть "
                f"(уровень {ref.level}).\n"
                f"Когда он оплатит — вы получите {10 if ref.level == 2 else 5}% от суммы."
            )
        await _send(bot, ref.tg_user_id, text)

    # 2) Admin — full info
    username_str = f"@{new_user_username}" if new_user_username else "—"
    name_str = new_user_first_name or "—"
    chain_lines = []
    for ref in referrers:
        ref_username = f"@{ref.tg_username}" if ref.tg_username else "—"
        ref_name = ref.tg_first_name or "—"
        chain_lines.append(
            f"  L{ref.level}: {ref_name} ({ref_username}) "
            f"[tg_id={ref.tg_user_id}, db_id={ref.id}]"
        )
    chain_text = "\n".join(chain_lines) if chain_lines else "  (нет рефереров)"

    admin_text = (
        "🆕 <b>Новый пользователь</b>\n\n"
        f"TG: {name_str} ({username_str})\n"
        f"tg_user_id: <code>{new_user_tg_id}</code>\n"
        f"Реф-код: <code>{ref_code}</code>\n"
        f"Цепь рефереров:\n{chain_text}\n\n"
        f"🔗 <a href=\"tg://user?id={new_user_tg_id}\">Открыть в Telegram</a>"
    )
    await _send(bot, s.admin_telegram_id, admin_text)


async def notify_payment(
    bot: Bot,
    paying_user_tg_id: int,
    paying_username: str | None,
    paying_first_name: str | None,
    amount_rub: float,
    description: str,
    referrers: list[tuple[ReferrerInfo, float]],
) -> None:
    """Notify referrers (anonymous, with their commission) + admin (full info)."""
    s = get_settings()
    if not s.admin_telegram_id:
        return

    # 1) Referrers — anonymous + their commission
    for ref, commission in referrers:
        if ref.tg_user_id is None:
            continue
        text = (
            f"💰 <b>Начисление по реферальной программе</b>\n\n"
            f"Кто-то из вашей L{ref.level} сети оплатил.\n"
            f"Сумма оплаты: <b>{amount_rub:.0f}₽</b>\n"
            f"Ваша комиссия ({['30', '10', '5'][ref.level-1]}%): "
            f"<b>+{commission:.0f}₽</b>\n\n"
            f"Баланс можно вывести через /referral"
        )
        await _send(bot, ref.tg_user_id, text)

    # 2) Admin — full info
    username_str = f"@{paying_username}" if paying_username else "—"
    name_str = paying_first_name or "—"
    chain_lines = []
    for ref, commission in referrers:
        ref_username = f"@{ref.tg_username}" if ref.tg_username else "—"
        ref_name = ref.tg_first_name or "—"
        chain_lines.append(
            f"  L{ref.level}: {ref_name} ({ref_username}) "
            f"[tg_id={ref.tg_user_id}, db_id={ref.id}] — +{commission:.0f}₽"
        )
    chain_text = "\n".join(chain_lines) if chain_lines else "  (нет рефереров)"

    admin_text = (
        "💰 <b>Новая оплата</b>\n\n"
        f"Плательщик: {name_str} ({username_str})\n"
        f"tg_user_id: <code>{paying_user_tg_id}</code>\n"
        f"Сумма: <b>{amount_rub:.0f}₽</b>\n"
        f"Описание: {description}\n\n"
        f"Цепь комиссий:\n{chain_text}\n\n"
        f"🔗 <a href=\"tg://user?id={paying_user_tg_id}\">Открыть в Telegram</a>"
    )
    await _send(bot, s.admin_telegram_id, admin_text)


async def notify_admin_custom(bot: Bot, text: str) -> None:
    """Send arbitrary text to admin. For /admin commands etc."""
    s = get_settings()
    if not s.admin_telegram_id:
        return
    await _send(bot, s.admin_telegram_id, text)
