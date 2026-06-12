"""GitHub connection flow via Device Flow.

Commands:
  /github        — show status (connected / not)
  /github connect — start Device Flow (user_code, polling)
  /github disconnect — wipe token
  /github pat    — fallback: paste Personal Access Token directly

Architecture:
  1. /github connect → start_device_flow() → показать user_code + verification_uri
  2. Edit message с "Жду подтверждения..." → запустить background-task poll_for_token
  3. По завершении → encrypt + store + edit "✅ Подключено как @username"
  4. /github disconnect → clear_user_github + edit "Отключено"
  5. /github pat    → ждать текст с токеном → encrypt + store
"""

from __future__ import annotations

import asyncio
import logging
from typing import cast

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.services import database
from bot.services.github_device_flow import (
    poll_for_token,
    start_device_flow,
    validate_token_and_get_username,
)
from bot.services.github_export import encrypt_token, validate_user_token

logger = logging.getLogger(__name__)

router = Router(name="github-auth")


class GitHubAuthFSM(StatesGroup):
    """FSM для GitHub Device Flow + PAT fallback."""

    waiting_for_pat = State()  # user pastes PAT inline
    waiting_for_device_confirm = State()  # user sees user_code, polls in background


# ============================================================================
# /github command
# ============================================================================


@router.message(F.text == "/github")
async def cmd_github(message: Message, state: FSMContext) -> None:
    """/github — показать статус подключения GitHub."""
    await state.clear()
    tg_id = message.from_user.id if message.from_user else None
    if not tg_id:
        return

    info = await database.get_user_github_info(tg_id)
    if not info:
        await message.answer(
            "✦ <b>GitHub</b>\n\n" "Не удалось получить данные. Попробуй позже."
        )
        return

    if info.get("connected"):
        username = info.get("github_username") or "?"
        connected_at = info.get("github_connected_at")
        date_str = connected_at.strftime("%d.%m.%Y %H:%M") if connected_at else "—"

        await message.answer(
            f"✦ <b>GitHub подключён</b>\n\n"
            f"Аккаунт: <b>@{username}</b>\n"
            f"Подключён: {date_str}\n\n"
            f"Теперь при публикации сайта бот может залить его в твой репозиторий "
            f"<code>{username}/buildo-sites</code>.\n\n"
            f"Что хочешь сделать?",
            reply_markup=_github_connected_kb(),
        )
    else:
        await message.answer(
            "✦ <b>GitHub</b>\n\n"
            "Пока не подключён. Подключи, чтобы публиковать сайты "
            "прямо в свой GitHub-репозиторий.\n\n"
            "<b>Два способа:</b>\n"
            "• <b>OAuth (рекомендую)</b> — 1 раз сходишь в GitHub, без токенов\n"
            "• <b>PAT</b> — создай Personal Access Token и пришли мне\n\n"
            "OAuth занимает ~15 секунд.",
            reply_markup=_github_disconnected_kb(),
        )


def _github_connected_kb():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔌 Отключить", callback_data="gh:disconnect")],
            [
                InlineKeyboardButton(
                    text="🔄 Переподключить", callback_data="gh:reconnect"
                ),
                InlineKeyboardButton(text="✏️ Ввести PAT", callback_data="gh:pat"),
            ],
            [InlineKeyboardButton(text="📋 В меню", callback_data="menu:home")],
        ]
    )


def _github_disconnected_kb():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ Подключить через GitHub", callback_data="gh:connect"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔑 Ввести PAT вручную", callback_data="gh:pat"
                ),
                InlineKeyboardButton(text="📋 В меню", callback_data="menu:home"),
            ],
        ]
    )


# ============================================================================
# OAuth Device Flow — connect / reconnect
# ============================================================================


@router.callback_query(F.data.in_({"gh:connect", "gh:reconnect"}))
async def cb_github_connect(callback: CallbackQuery, state: FSMContext) -> None:
    """Start GitHub Device Flow."""
    await callback.answer()
    if not callback.message:
        return

    await state.clear()
    msg = cast(Message, callback.message)
    info_text = (
        "🔄 <b>Переподключаю GitHub</b>\n\n"
        if callback.data == "gh:reconnect"
        else "⚡ <b>Подключаю GitHub</b>\n\n"
    )

    await msg.edit_text(info_text + "Запрашиваю код у GitHub…")

    try:
        data = await start_device_flow()
    except Exception as exc:  # noqa: BLE001
        logger.exception("device flow start failed")
        await msg.edit_text(
            f"⚠️ <b>Не удалось начать авторизацию</b>\n\n"
            f"<code>{exc}</code>\n\n"
            f"Попробуй позже или используй ручной ввод PAT.",
            reply_markup=_github_disconnected_kb(),
        )
        return

    # Сохраняем state
    await state.update_data(
        github_device_code=data["device_code"],
        github_user_code=data["user_code"],
        github_interval=data.get("interval", 5),
        github_expires_in=data.get("expires_in", 900),
    )
    await state.set_state(GitHubAuthFSM.waiting_for_device_confirm)

    user_code = data["user_code"]
    verification_uri = data.get("verification_uri", "https://github.com/login/device")

    await msg.edit_text(
        "⚡ <b>Подключи GitHub</b>\n\n"
        f'1️⃣ Открой <a href="{verification_uri}">{verification_uri}</a>\n'
        f"2️⃣ Введи код: <code>{user_code}</code>\n\n"
        f"⏳ Я подожду 15 минут. Как подтвердишь — сам подключусь.\n\n"
        f"<i>Не закрывай это сообщение — здесь появится результат.</i>",
        reply_markup=_github_polling_kb(),
    )

    # Запускаем background polling
    asyncio.create_task(
        _poll_and_finalize(
            tg_id=callback.from_user.id if callback.from_user else 0,
            device_code=data["device_code"],
            interval=data.get("interval", 5),
            expires_in=data.get("expires_in", 900),
            chat_id=msg.chat.id,
            message_id=msg.message_id,
        )
    )


def _github_polling_kb():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔑 Ввести PAT вместо OAuth", callback_data="gh:pat"
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="gh:cancel")],
        ]
    )


@router.callback_query(F.data == "gh:cancel")
async def cb_github_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    if callback.message:
        try:
            await cast(Message, callback.message).edit_text(
                "✦ <b>GitHub</b>\n\nОтменено.",
                reply_markup=_github_disconnected_kb(),
            )
        except Exception:  # noqa: BLE001
            pass


async def _poll_and_finalize(
    tg_id: int,
    device_code: str,
    interval: int,
    expires_in: int,
    chat_id: int,
    message_id: int,
) -> None:
    """Background-task: поллит GitHub, по завершении редактирует сообщение."""
    from aiogram import Bot

    from bot.config import get_settings

    s = get_settings()
    bot = Bot(token=s.telegram_bot_token)

    try:
        result = await poll_for_token(
            device_code=device_code,
            interval=interval,
            expires_in=expires_in,
        )
    except asyncio.TimeoutError:
        await _edit_poll_message(
            bot,
            chat_id,
            message_id,
            "⏰ <b>Время вышло</b>\n\n"
            "GitHub Device Flow истёк (15 мин). Запусти /github connect заново.",
            _github_disconnected_kb(),
        )
        return
    except PermissionError as exc:
        await _edit_poll_message(
            bot,
            chat_id,
            message_id,
            f"🚫 <b>Авторизация отменена</b>\n\n{exc}",
            _github_disconnected_kb(),
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("device flow poll failed")
        await _edit_poll_message(
            bot,
            chat_id,
            message_id,
            f"⚠️ <b>Ошибка авторизации</b>\n\n<code>{exc}</code>",
            _github_disconnected_kb(),
        )
        return

    # Успех — получили access_token
    access_token = result["access_token"]

    # 1) Валидируем и получаем username
    try:
        username = await validate_token_and_get_username(access_token)
    except Exception as exc:  # noqa: BLE001
        logger.exception("validate_token failed")
        await _edit_poll_message(
            bot,
            chat_id,
            message_id,
            f"⚠️ <b>Token получен, но не валиден</b>\n\n<code>{exc}</code>",
            _github_disconnected_kb(),
        )
        return

    if not username:
        await _edit_poll_message(
            bot,
            chat_id,
            message_id,
            "⚠️ <b>Не удалось получить username</b>\n\n"
            "GitHub вернул пустой login. Попробуй ещё раз.",
            _github_disconnected_kb(),
        )
        return

    # 2) Encrypt + store
    encrypted = encrypt_token(access_token)
    ok = await database.set_user_github_oauth(tg_id, encrypted, username)
    if not ok:
        await _edit_poll_message(
            bot,
            chat_id,
            message_id,
            "⚠️ <b>Не удалось сохранить</b>\n\n" "БД вернула ошибку. Попробуй позже.",
            _github_disconnected_kb(),
        )
        return

    await _edit_poll_message(
        bot,
        chat_id,
        message_id,
        f"✅ <b>GitHub подключён!</b>\n\n"
        f"Аккаунт: <b>@{username}</b>\n\n"
        f"Теперь бот может заливать сайты в репозиторий "
        f"<code>{username}/buildo-sites</code>.\n\n"
        f"<i>Это сообщение можно удалить — оно больше не нужно.</i>",
        _github_connected_kb(),
    )


async def _edit_poll_message(bot, chat_id, message_id, text, reply_markup):
    try:
        await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001
        pass


# ============================================================================
# PAT fallback — manual token paste
# ============================================================================


@router.callback_query(F.data == "gh:pat")
async def cb_github_pat(callback: CallbackQuery, state: FSMContext) -> None:
    """Юзер выбрал ручной ввод PAT."""
    await callback.answer()
    if not callback.message:
        return

    await state.set_state(GitHubAuthFSM.waiting_for_pat)

    await cast(Message, callback.message).edit_text(
        "🔑 <b>Personal Access Token</b>\n\n"
        '1️⃣ Открой <a href="https://github.com/settings/tokens/new'
        '?scopes=repo&description=Buildo">github.com/settings/tokens/new</a>\n'
        "2️⃣ Поставь галочку <b>repo</b> (полный доступ к репозиториям)\n"
        "3️⃣ Нажми <b>Generate token</b> и скопируй его\n"
        "4️⃣ Пришли его мне <b>следующим сообщением</b>\n\n"
        "⚠️ <b>Важно:</b> после отправки удали это сообщение у себя — "
        "токен будет виден в истории чата.\n\n"
        "<i>Для отмены — /github</i>",
    )


@router.message(GitHubAuthFSM.waiting_for_pat, ~F.text.startswith("/"))
async def receive_pat(message: Message, state: FSMContext) -> None:
    """Получили PAT — валидируем, encrypt, store."""
    if not message.text or not message.from_user:
        await message.answer("Пришли токен текстом, пожалуйста.")
        return

    # Удаляем сообщение юзера (там токен) — best effort
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    token = message.text.strip()
    if not token or len(token) < 20:
        await message.answer(
            "⚠️ Это не похоже на токен. Попробуй ещё раз или /github для отмены."
        )
        return

    # 1) Validate
    result = await validate_user_token(token)
    if not result["valid"]:
        await message.answer(
            f"⚠️ <b>Токен невалиден</b>\n\n"
            f"<code>{result.get('error', '?')[:200]}</code>\n\n"
            f"Проверь scopes (нужен <b>repo</b>) и срок действия.",
        )
        return

    username = result["username"]
    if not username:
        await message.answer("⚠️ GitHub не вернул username. Странно.")
        return

    # 2) Encrypt + store
    encrypted = encrypt_token(token)
    ok = await database.set_user_github_oauth(message.from_user.id, encrypted, username)
    if not ok:
        await message.answer("⚠️ Не удалось сохранить в БД. Попробуй позже.")
        return

    await state.clear()
    await message.answer(
        f"✅ <b>GitHub подключён!</b>\n\n"
        f"Аккаунт: <b>@{username}</b>\n\n"
        f"Можешь публиковать сайты — бот зальёт их в "
        f"<code>{username}/buildo-sites</code>.",
        reply_markup=_github_connected_kb(),
    )


# ============================================================================
# Disconnect
# ============================================================================


@router.callback_query(F.data == "gh:disconnect")
async def cb_github_disconnect(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    if not callback.message or not callback.from_user:
        return

    ok = await database.clear_user_github(callback.from_user.id)
    if not ok:
        await cast(Message, callback.message).edit_text(
            "⚠️ Не удалось отключить. Попробуй позже."
        )
        return

    await cast(Message, callback.message).edit_text(
        "🔌 <b>GitHub отключён</b>\n\n"
        "Токен удалён. Сайты можно по-прежнему публиковать, "
        "но бот не сможет залить их в твой GitHub.",
        reply_markup=_github_disconnected_kb(),
    )
