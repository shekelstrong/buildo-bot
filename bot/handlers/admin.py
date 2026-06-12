"""/admin — admin-only commands. Single-admin MVP (ADMIN_TG_ID).

Includes AI-agent /admin_edit which lets the admin describe a small
textual change in Russian, get a proposal back, then /admin_apply to
commit + push (CI/CD then deploys).
"""

from __future__ import annotations

import logging
from typing import cast

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


# ============== Inline button callbacks (from /admin main menu) ==============


@router.callback_query(F.data == "admin:edit")
async def cb_admin_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Start AI-edit flow — admin will be asked to describe the change."""
    await state.set_state(AdminEditFlow.waiting_for_request)
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    try:
        await msg.edit_text(
            "✦ <b>AI-агент правок</b>\n\n"
            "Опиши словами, что нужно изменить в боте. Например:\n\n"
            "• <i>«Добавь в /start приветствие с эмодзи 🚀»</i>\n"
            "• <i>«Поменяй текст ошибки в /site на более мягкий»</i>\n"
            "• <i>«Увеличь free_sites_limit до 3 в базе»</i>\n\n"
            "AI составит план изменений, ты его проверишь и подтвердишь.\n"
            "Затем бот сам сделает commit + push в GitHub → CI/CD задеплоит.\n\n"
            "/cancel — отмена"
        )
    except Exception:  # noqa: BLE001
        pass
    await callback.answer("Опиши правку")


@router.message(AdminEditFlow.waiting_for_request)
async def receive_edit_request(message: Message, state: FSMContext) -> None:
    """Admin sent a change description — call LLM, show proposal."""
    if message.text is None or not message.text.strip():
        await message.answer("Опиши правку текстом.")
        return
    request_text = message.text.strip()
    thinking = await message.answer("✦ Думаю над правкой... (10-30 секунд)")
    try:
        proposal = await bot_self_editor.propose_edit(request_text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("propose_edit failed")
        await thinking.edit_text(f"✗ Ошибка: <code>{exc}</code>")
        return

    if not proposal.edits:
        await thinking.edit_text(
            f"✦ <b>AI не смог предложить правку</b>\n\n"
            f"<i>{proposal.summary}</i>\n\n"
            f"Попробуй переформулировать или сделать вручную через SSH."
        )
        await state.clear()
        return

    # Show diff preview
    lines = ["✦ <b>Предложение AI-агента</b>\n", f"<i>{proposal.summary}</i>\n"]
    for i, edit in enumerate(proposal.edits, 1):
        old_preview = edit.old_string[:200] + (
            "..." if len(edit.old_string) > 200 else ""
        )
        new_preview = edit.new_string[:200] + (
            "..." if len(edit.new_string) > 200 else ""
        )
        lines.append(
            f"\n<b>Правка #{i}:</b> <code>{edit.file_path}</code>\n"
            f"<b>Было:</b>\n<pre>{old_preview}</pre>\n"
            f"<b>Стало:</b>\n<pre>{new_preview}</pre>"
        )
    lines.append("\nПрименить?")

    await state.update_data(
        proposal_summary=proposal.summary, proposal_edits_count=len(proposal.edits)
    )
    await state.set_state(AdminEditFlow.reviewing_proposal)
    # Cache proposal in state (small enough — limit 5 edits)
    await state.update_data(
        _proposal_edits=[
            {
                "file_path": e.file_path,
                "old_string": e.old_string,
                "new_string": e.new_string,
            }
            for e in proposal.edits
        ]
    )
    await thinking.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Применить и задеплоить", callback_data="admin:apply"
                    ),
                    InlineKeyboardButton(
                        text="✗ Отменить", callback_data="admin:cancel"
                    ),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "admin:apply")
async def cb_admin_apply(callback: CallbackQuery, state: FSMContext) -> None:
    """Apply the cached proposal: write to disk, git commit, git push."""
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    data = await state.get_data()
    cached = data.get("_proposal_edits")
    summary = data.get("proposal_summary", "(без описания)")
    if not cached:
        await callback.answer("Нет активной правки", show_alert=True)
        return

    # Reconstruct proposal
    from bot.services.bot_self_editor import EditProposal, ProposedEdit

    proposal = EditProposal(
        summary=summary,
        edits=[ProposedEdit(**e) for e in cached],
    )
    await msg.edit_text("⏳ Применяю и пушу в GitHub...")
    try:
        result = await bot_self_editor.apply_and_commit(proposal)
    except Exception as exc:  # noqa: BLE001
        logger.exception("apply_and_commit failed")
        await msg.edit_text(f"✗ Ошибка применения: <code>{exc}</code>")
        await state.clear()
        await callback.answer("Ошибка")
        return

    if not result.success:
        await msg.edit_text(
            f"✗ <b>Не получилось применить</b>\n\n"
            f"<code>{result.error[:500]}</code>\n\n"
            f"Попробуй вручную или /admin — начать заново."
        )
        await state.clear()
        await callback.answer("Ошибка")
        return

    sha_short = result.commit_sha[:7] if result.commit_sha else "?"
    await msg.edit_text(
        f"✦ <b>Готово!</b>\n\n"
        f"Применено правок: <b>{result.applied_count}</b>\n"
        f"Commit: <code>{sha_short}</code>\n"
        f"Push в GitHub: ✅\n\n"
        f"CI/CD подхватит и задеплоит через ~1-2 минуты.\n"
        f"Я пришлю уведомление когда деплой завершится (если настроишь webhook).",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📋 В админку", callback_data="admin:home")]
            ]
        ),
    )
    await state.clear()
    await callback.answer("Применено!")


@router.callback_query(F.data == "admin:cancel")
async def cb_admin_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel the AI-edit flow without applying."""
    await state.clear()
    if callback.message is not None:
        msg = cast(Message, callback.message)
        try:
            await msg.edit_text(
                "✗ Отменил. /admin — назад в админку.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📋 В админку", callback_data="admin:home"
                            )
                        ]
                    ]
                ),
            )
        except Exception:  # noqa: BLE001
            pass
    await callback.answer("Отменил")


@router.callback_query(F.data == "admin:home")
async def cb_admin_home(callback: CallbackQuery) -> None:
    """Return to admin main menu."""
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    try:
        await msg.edit_text(
            "🔐 <b>Buildo Admin Panel</b>\n\n"
            "• ✦ AI-агент правок — описать правку, бот сам коммитит в GitHub\n"
            "• 📊 Статистика — юзеры, сайты, платежи\n"
            "• 👥 Юзеры — последние 50\n"
            "• 💰 Платежи — последние 20",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✦ AI-агент правок", callback_data="admin:edit"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="📊 Статистика", callback_data="admin:stats"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="👥 Юзеры", callback_data="admin:users"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="💰 Платежи", callback_data="admin:pays"
                        )
                    ],
                ]
            ),
        )
    except Exception:  # noqa: BLE001
        pass
    await callback.answer()


@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(callback: CallbackQuery) -> None:
    """Inline button: stats."""
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    stats = await admin_service.get_stats()
    try:
        await msg.edit_text(
            "📊 <b>Статистика Buildo</b>\n\n"
            f"👥 Юзеров всего: <b>{stats['users_total']}</b>\n"
            f"🆕 Новых за 24ч: <b>{stats['users_24h']}</b>\n"
            f"🌐 Сайтов создано: <b>{stats['sites_total']}</b>\n"
            f"🚀 Задеплоено: <b>{stats['sites_deployed']}</b>\n"
            f"💰 Платежей (₽): <b>{stats['revenue_rub']}</b>\n"
            f"⭐ Telegram Stars: <b>{stats['revenue_stars']}</b>\n"
            f"🪙 CryptoBot: <b>{stats['revenue_crypto_usd']}</b> USD",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📋 В админку", callback_data="admin:home"
                        )
                    ]
                ]
            ),
        )
    except Exception:  # noqa: BLE001
        pass
    await callback.answer()


@router.callback_query(F.data == "admin:users")
async def cb_admin_users(callback: CallbackQuery) -> None:
    """Inline button: users list."""
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    users = await admin_service.get_recent_users(limit=20)
    if not users:
        try:
            await msg.edit_text(
                "Юзеров пока нет.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📋 В админку", callback_data="admin:home"
                            )
                        ]
                    ]
                ),
            )
        except Exception:  # noqa: BLE001
            pass
        await callback.answer()
        return
    lines = ["👥 <b>Последние юзеры (20)</b>\n"]
    for u in users:
        kind = u.get("kind", "site")
        lines.append(
            f"• <code>{u['id']}</code> — @{u.get('username', '—')} "
            f"<i>({u.get('first_name', '—')})</i> — {kind}"
        )
    try:
        await msg.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📋 В админку", callback_data="admin:home"
                        )
                    ]
                ]
            ),
        )
    except Exception:  # noqa: BLE001
        pass
    await callback.answer()


@router.callback_query(F.data == "admin:pays")
async def cb_admin_pays(callback: CallbackQuery) -> None:
    """Inline button: payments list."""
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    pays = await admin_service.get_recent_payments(limit=20)
    if not pays:
        try:
            await msg.edit_text(
                "Платежей пока нет.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📋 В админку", callback_data="admin:home"
                            )
                        ]
                    ]
                ),
            )
        except Exception:  # noqa: BLE001
            pass
        await callback.answer()
        return
    lines = ["💰 <b>Последние платежи (20)</b>\n"]
    for p in pays:
        lines.append(
            f"• {p['created_at'][:19]} — <b>{p['amount']} {p['currency']}</b> "
            f"({p['provider']}) — user <code>{p['user_id']}</code>"
        )
    try:
        await msg.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📋 В админку", callback_data="admin:home"
                        )
                    ]
                ]
            ),
        )
    except Exception:  # noqa: BLE001
        pass
    await callback.answer()


@router.callback_query(F.data == "admin:noop")
async def noop_callback(callback: CallbackQuery) -> None:
    """Placeholder for admin keyboard noop."""
    if callback.message is not None:
        await callback.answer("noop")
