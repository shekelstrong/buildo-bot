"""/admin — admin-only commands. Single-admin MVP (ADMIN_TG_ID).

Includes AI-agent /admin_edit which lets the admin describe a small
textual change in Russian, get a proposal back, then /admin_apply to
commit + push (CI/CD then deploys).
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.services import admin as admin_service
from bot.services import bot_self_editor

logger = logging.getLogger(__name__)

router = Router(name="admin")


class AdminEditFlow(StatesGroup):
    """FSM for AI-edit flow."""

    waiting_for_request = State()
    reviewing_proposal = State()


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    """Admin menu — only ADMIN_TG_ID gets here (filter on router)."""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✦ AI-агент правок", callback_data="admin:edit"
                )
            ],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="👥 Юзеры", callback_data="admin:users")],
            [InlineKeyboardButton(text="💰 Платежи", callback_data="admin:pays")],
        ]
    )
    text = (
        "🔐 <b>Buildo Admin Panel</b>\n\n"
        "<b>Кнопки:</b>\n"
        "• ✦ AI-агент правок — описать правку, бот сам коммитит в GitHub\n"
        "• 📊 Статистика — юзеры, сайты, платежи\n"
        "• 👥 Юзеры — последние 50\n"
        "• 💰 Платежи — последние 20\n\n"
        "<b>Команды:</b>\n"
        "/admin_stats /admin_users /admin_payments\n"
        "/admin_broadcast /admin_redeploy /admin_kill /admin_logs"
    )
    await message.answer(text, reply_markup=kb)


@router.message(Command("admin_stats"))
async def cmd_stats(message: Message) -> None:
    stats = await admin_service.get_stats()
    await message.answer(
        "📊 <b>Статистика Buildo</b>\n\n"
        f"👥 Юзеров всего: <b>{stats['users_total']}</b>\n"
        f"🆕 Новых за 24ч: <b>{stats['users_24h']}</b>\n"
        f"🌐 Сайтов создано: <b>{stats['sites_total']}</b>\n"
        f"🚀 Задеплоено: <b>{stats['sites_deployed']}</b>\n"
        f"💰 Платежей (₽): <b>{stats['revenue_rub']}</b>\n"
        f"⭐ Telegram Stars: <b>{stats['revenue_stars']}</b>\n"
        f"🪙 CryptoBot: <b>{stats['revenue_crypto_usd']}</b> USD\n\n"
        "<i>Данные заглушечные до подключения Supabase</i>"
    )


@router.message(Command("admin_users"))
async def cmd_users(message: Message) -> None:
    users = await admin_service.get_recent_users(limit=50)
    if not users:
        await message.answer("Юзеров пока нет.")
        return
    lines = ["👥 <b>Последние юзеры</b>\n"]
    for u in users:
        kind = u.get("kind", "site")
        lines.append(
            f"• <code>{u['id']}</code> — @{u.get('username', '—')} "
            f"<i>({u.get('first_name', '—')})</i> — kind: <b>{kind}</b>"
        )
    await message.answer("\n".join(lines))


@router.message(Command("admin_payments"))
async def cmd_payments(message: Message) -> None:
    pays = await admin_service.get_recent_payments(limit=20)
    if not pays:
        await message.answer("Платежей пока нет.")
        return
    lines = ["💰 <b>Последние платежи</b>\n"]
    for p in pays:
        lines.append(
            f"• {p['created_at']} — <b>{p['amount']} {p['currency']}</b> "
            f"({p['provider']}) — user <code>{p['user_id']}</code>"
        )
    await message.answer("\n".join(lines))


@router.message(Command("admin_broadcast"))
async def cmd_broadcast(message: Message) -> None:
    """Stage a broadcast — admin sends text after this command, we send to all."""
    if message.text is None or message.text.strip() == "/admin_broadcast":
        await message.answer(
            "📣 <b>Рассылка</b>\n\n"
            "Пришли текст одним сообщением после команды. Например:\n"
            "<code>/admin_broadcast\nПривет! Сегодня выходной, но мы работаем 🤖</code>"
        )
        return
    # Strip command, take the rest as broadcast body
    body = (
        message.text.split(maxsplit=1)[1]
        if len(message.text.split(maxsplit=1)) > 1
        else ""
    )
    if not body.strip():
        await message.answer("Пустой текст — пришли непустое сообщение.")
        return
    # In Phase 1 this iterates over Supabase users table. Stub returns count=0.
    count = await admin_service.broadcast(body)
    await message.answer(f"📣 Разослано: <b>{count}</b> юзерам.")


@router.message(Command("admin_redeploy"))
async def cmd_redeploy(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование: <code>/admin_redeploy &lt;site_id&gt;</code>"
        )
        return
    site_id = args[1].strip()
    result = await admin_service.redeploy(site_id)
    await message.answer(f"🚀 Re-деплой <code>{site_id}</code>: <b>{result}</b>")


@router.message(Command("admin_kill"))
async def cmd_kill(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: <code>/admin_kill &lt;user_id&gt;</code>")
        return
    try:
        target_id = int(args[1].strip())
    except ValueError:
        await message.answer("user_id должен быть числом.")
        return
    if target_id == message.from_user.id:  # type: ignore[union-attr]
        await message.answer("Сам себя забанить нельзя.")
        return
    result = await admin_service.ban_user(target_id)
    await message.answer(f"🚫 Юзер <code>{target_id}</code>: <b>{result}</b>")


@router.message(Command("admin_logs"))
async def cmd_logs(message: Message) -> None:
    """Tail last 30 log lines (stub returns a sample)."""
    await message.answer(
        "📜 <b>Последние логи (заглушка)</b>\n\n"
        "<pre>"
        "2026-06-11 12:00:00 INFO  bot: Buildo bot starting (env=production)\n"
        "2026-06-11 12:00:01 INFO  aiogram.dispatcher: Start polling\n"
        "</pre>\n\n"
        "В Phase 1 будет tail реальных логов из Docker."
    )


@router.callback_query(F.data == "admin:noop")
async def noop_callback(callback: CallbackQuery) -> None:
    """Placeholder for admin keyboard noop."""
    if callback.message is not None:
        await callback.answer("noop")
