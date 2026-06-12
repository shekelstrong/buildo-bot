"""Common handlers — /start, /help, fallback, and main-menu callback buttons.

All replies send a scene image first (where appropriate), then a styled
text message with premium typography and inline buttons.
"""

from __future__ import annotations

import logging
from typing import cast

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.services import notifications
from bot.services.referral import record_signup
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
            caption="",
        )
    except Exception:  # noqa: BLE001
        logger.exception("scene %s send failed", scene_name)


# ============== /start with deep-link ref code ==============


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(message: Message, command) -> None:  # type: ignore[no-untyped-def]
    """Greet user with welcome scene + main menu. Handles /start ref_CODE."""
    if message.from_user is None:
        return
    name = message.from_user.first_name or "друг"

    # Handle deep-link ref code (e.g. /start ref_ABC123)
    ref_code: str | None = None
    args = getattr(command, "args", None)
    if args and args.startswith("ref_"):
        ref_code = args[4:].strip()
        if not ref_code:
            ref_code = None

    if ref_code:
        try:
            referrers = await record_signup(
                new_user_tg_id=message.from_user.id,
                new_user_username=message.from_user.username,
                new_user_first_name=message.from_user.first_name,
                ref_code=ref_code,
            )
            if message.bot:
                await notifications.notify_signup(
                    bot=message.bot,
                    referrers=referrers,
                    new_user_tg_id=message.from_user.id,
                    new_user_username=message.from_user.username,
                    new_user_first_name=message.from_user.first_name,
                    ref_code=ref_code,
                )
        except Exception:  # noqa: BLE001
            logger.exception("referral signup failed")

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


# ============== /help ==============


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "✦ <b>Справка Buildo</b>\n\n"
        "<b>Основные команды:</b>\n"
        "/start — главное меню\n"
        "/site — создать новый сайт\n"
        "/sites — список моих сайтов\n"
        "/referral — реферальная программа\n"
        "/articles — SEO-статьи и блог (фаза 1.5)\n"
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
        "Вопросы — пиши прямо сюда в чат.",
        reply_markup=_main_keyboard(),
    )


# ============== /cancel / "в меню" / "отмена" ==============


@router.message(Command("cancel"))
@router.message(F.text.casefold() == "отмена")
@router.message(F.text.casefold() == "в меню")
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Reset any active FSM state. 'В меню' as a phrase also works."""
    await state.clear()
    await message.answer(
        "✦ <b>Главное меню</b>\n\nВыбери действие или напиши что хочешь сделать.",
        reply_markup=_main_keyboard(),
    )


# ============== Main menu inline-button callbacks ==============


@router.callback_query(F.data == "menu:home")
async def cb_menu_home(callback: CallbackQuery, state: FSMContext) -> None:
    """Return to main menu from any screen."""
    await state.clear()
    if callback.message is not None:
        msg = cast(Message, callback.message)
        try:
            await msg.edit_text(
                "✦ <b>Главное меню</b>\n\nВыбери действие или напиши что хочешь сделать.",
                reply_markup=_main_keyboard(),
            )
        except Exception:  # noqa: BLE001
            pass
    await callback.answer("Главное меню")


@router.callback_query(F.data == "menu:site")
async def cb_menu_site(callback: CallbackQuery, state: FSMContext) -> None:
    """Inline button 'Создать сайт' — runs /site flow (sets FSM state)."""
    # Lazy import to avoid circular dependency at module load
    from bot.handlers.site_builder import SiteFlow

    await state.clear()
    await state.set_state(SiteFlow.waiting_for_prompt)
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    try:
        await _send_scene(msg, "generating")
    except Exception:  # noqa: BLE001
        pass
    await msg.answer(
        "✦ <b>Создаём новый сайт</b>\n\n"
        "Опиши словами, что нужно сделать. Чем подробнее — тем точнее результат.\n\n"
        "Примеры:\n"
        "<i>«лендинг для кофейни в центре Москвы, тёплый минимализм, "
        "секции: hero, меню, контакты»</i>\n"
        "<i>«портфолио веб-дизайнера с кейсами и контактами, тёмная тема»</i>\n"
        "<i>«сайт-визитка для автосервиса, серьёзный стиль, форма записи»</i>\n\n"
        "/cancel — отмена"
    )
    await callback.answer("Опиши сайт")


@router.callback_query(F.data == "menu:sites")
async def cb_menu_sites(callback: CallbackQuery) -> None:
    """Inline button 'Мои сайты' — calls /sites logic."""
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    tg_id = callback.from_user.id if callback.from_user else None
    if tg_id is None:
        await callback.answer()
        return
    # Lazy import to avoid circular dependency
    from bot.services import supabase

    try:
        user = await supabase.upsert_tg_user(tg_user_id=tg_id)
        if user is None:
            await _send_scene(msg, "error")
            await msg.answer("📦 <b>Мои сайты</b>\n\n<i>БД недоступна.</i>")
            await callback.answer()
            return
        user_id_raw = user.get("id") if isinstance(user, dict) else user["id"]
        if user_id_raw is None:
            await msg.answer("✗ Ошибка user_id")
            await callback.answer()
            return
        user_id = int(user_id_raw)
        sites = await supabase.list_user_sites(user_id, limit=20)
        if not sites:
            await _send_scene(msg, "no_sites")
            await msg.answer(
                "📦 <b>Мои сайты</b>\n\n"
                "<i>У тебя пока нет сайтов.</i>\n\n"
                "Жми «✦ Создать сайт» чтобы начать.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✦ Создать сайт", callback_data="menu:site"
                            )
                        ]
                    ]
                ),
            )
            await callback.answer()
            return
        await _send_scene(msg, "menu")
        lines = ["📦 <b>Мои сайты</b>\n"]
        for s in sites[:20]:
            name = s.get("project_name") or "Без названия"
            url = s.get("deploy_url") or ""
            status = s.get("status") or "—"
            ts = (s.get("last_deploy_at") or "")[:19]
            if url:
                lines.append(f"• <b>{name}</b> · {status} · {ts}\n  🔗 {url}")
            else:
                lines.append(f"• <b>{name}</b> · {status} · {ts}")
        await msg.answer(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✦ Создать ещё", callback_data="menu:site"
                        ),
                        InlineKeyboardButton(
                            text="🔄 Обновить", callback_data="menu:sites"
                        ),
                    ]
                ]
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("cb_menu_sites failed")
        await _send_scene(msg, "error")
        await msg.answer(f"✗ Ошибка: <code>{exc}</code>")
    await callback.answer()


@router.callback_query(F.data == "menu:articles")
async def cb_menu_articles(callback: CallbackQuery) -> None:
    """Inline button 'SEO-статьи' — show articles menu (Phase 1.5 placeholder)."""
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    await msg.answer(
        "✦ <b>SEO-статьи и блог</b>\n\n"
        "<i>Фаза 1.5 — скоро!</i>\n\n"
        "Здесь будет:\n"
        "• AI-генерация статей 2000+ слов\n"
        "• SEO/GEO/AEO оптимизация\n"
        "• Автопубликация по расписанию\n"
        "• Связка с твоим сайтом (импорт статей в /articles секцию)\n\n"
        "Пока можно использовать вручную через /admin.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📋 В меню", callback_data="menu:home")]
            ]
        ),
    )
    await callback.answer("Скоро")


@router.callback_query(F.data == "menu:referral")
async def cb_menu_referral(callback: CallbackQuery) -> None:
    """Inline button 'Рефералы' — runs /referral logic."""
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    # Lazy import — referral handler isn't imported here to avoid cycle
    from bot.services.referral import get_referral_stats
    from bot.services.referral import make_bot_link

    tg_id = callback.from_user.id if callback.from_user else None
    if tg_id is None:
        await callback.answer()
        return
    stats = await get_referral_stats(tg_id)
    if stats is None:
        await msg.answer("✗ Не удалось получить реферальную ссылку. Попробуй позже.")
        await callback.answer()
        return

    try:
        png = get_scene("referral")
        await msg.answer_photo(
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
        "• L3 — 5% с оплат на 3-м уровне"
    )

    share_text = (
        "✦ Создай сайт через AI за минуту — https://t.me/buildo_aibot\n"
        f"Бонус по моей ссылке: {stats.bot_url}"
    )
    share_url = (
        "https://t.me/share/url?url="
        + make_bot_link(stats.code)
        + "&text="
        + share_text.replace(" ", "%20").replace("\n", "%0A")
    )
    await msg.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📤 Поделиться", url=share_url)],
                [InlineKeyboardButton(text="📋 В меню", callback_data="menu:home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:help")
async def cb_menu_help(callback: CallbackQuery) -> None:
    """Inline button 'Помощь' — runs /help logic."""
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    try:
        await msg.edit_text(
            "✦ <b>Справка Buildo</b>\n\n"
            "<b>Основные команды:</b>\n"
            "/start — главное меню\n"
            "/site — создать новый сайт\n"
            "/sites — список моих сайтов\n"
            "/referral — реферальная программа\n"
            "/articles — SEO-статьи\n"
            "/help — эта справка\n\n"
            "Вопросы — пиши прямо сюда в чат.",
            reply_markup=_main_keyboard(),
        )
    except Exception:  # noqa: BLE001
        pass
    await callback.answer()


# ============== Fallback ==============


@router.message()
async def fallback(message: Message) -> None:
    """Catch-all for unrecognized messages — guide user to commands."""
    if message.text and message.text.startswith("/"):
        return
    await message.answer(
        "✦ Это пока вне моего скоупа.\n\n"
        "Попробуй:\n"
        "• <b>/site</b> — создать сайт\n"
        "• Опиши задачу словами: <i>«сделай лендинг для кофейни»</i>\n"
        "• Или используй кнопки из /start"
    )
