"""Common handlers — /start, /help, fallback, and main-menu callback buttons.

Every reply that includes a scene image is sent as ONE message:
`answer_photo(caption=text, reply_markup=...)` so the user sees a
single combined bubble (image + caption + inline buttons), not two
separate messages.

Telegram's caption limit is 1024 characters — all captions below are
designed to fit. If a render fails or the scene image is missing, the
function falls back to a plain text `answer()` so the user always sees
the menu.
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


# ============== Helpers ==============


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
            [
                InlineKeyboardButton(text="🐙 GitHub", callback_data="menu:github"),
                InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu:help"),
            ],
        ]
    )


async def _send_scene_with_text(
    message: Message,
    scene_name: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML",
) -> None:
    """Send a scene PNG as a photo WITH caption + buttons as ONE message.

    Falls back to a plain-text `answer()` if:
      - the scene PNG cannot be rendered (libcairo missing, etc.)
      - Telegram rejects the photo (e.g. IMAGE_PROCESS_FAILED)
    In both cases the user sees exactly ONE message — either photo+caption
    or text+buttons, never two separate bubbles.
    """
    try:
        png = get_scene(scene_name)
    except Exception:  # noqa: BLE001
        logger.exception("scene %s render failed", scene_name)
        png = None

    if png:
        try:
            await message.answer_photo(
                photo=BufferedInputFile(png, filename=f"{scene_name}.png"),
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return
        except Exception:  # noqa: BLE001
            logger.exception("answer_photo with caption failed, falling back to text")

    # Fallback: single plain-text message (no photo)
    await message.answer(
        caption,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )


# Backwards-compat alias for code that still calls _send_scene(...)
async def _send_scene(message: Message, scene_name: str) -> None:
    """Legacy helper — sends an empty photo (no caption).

    New code should use `_send_scene_with_text` so the photo and the
    text+buttons arrive as ONE message.
    """
    try:
        png = get_scene(scene_name)
    except Exception:  # noqa: BLE001
        logger.exception("scene %s render failed", scene_name)
        return
    if not png:
        return
    try:
        await message.answer_photo(
            photo=BufferedInputFile(png, filename=f"{scene_name}.png"),
            caption="",
        )
    except Exception:  # noqa: BLE001
        logger.exception("scene %s send failed", scene_name)


# ============== Global /start reset (must come BEFORE any state-bound handlers) ==============


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(message: Message, command, state: FSMContext) -> None:  # type: ignore[no-untyped-def]
    """Greet user with welcome scene + main menu. Handles /start ref_CODE.

    Always resets FSM state first so /start works as a universal "back to menu"
    escape hatch from any state (editing, waiting_for_prompt, etc).
    """
    if message.from_user is None:
        return
    name = message.from_user.first_name or "друг"

    # CRITICAL: always reset state on /start
    await state.clear()

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

    # ONE message: scene PNG + caption text + inline buttons
    caption = (
        f"✦ <b>Привет, {name}!</b>\n\n"
        "Я <b>Buildo</b> — AI-платформа для создания сайтов.\n"
        "Опиши словами, что нужно — я сгенерирую код и задеплою за тебя.\n\n"
        "<b>Что умею:</b>\n"
        "• ✦ Создать сайт по твоему описанию\n"
        "• ✦ Задеплоить автоматически (GitHub / Layero)\n"
        "• ✦ Внести правки через чат (диалог-агент)\n"
        "• ✦ Вести SEO-блог с AI\n"
        "• ✦ Привести друзей и заработать"
    )
    await _send_scene_with_text(
        message, "welcome", caption, reply_markup=_main_keyboard()
    )


# ============== /help ==============


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Send help as a single text message (no scene image)."""
    await message.answer(
        "✦ <b>Справка Buildo</b>\n\n"
        "<b>Как создать сайт:</b>\n"
        "1. Жми «✦ Создать сайт» в меню\n"
        "2. Опиши словами, что нужно\n"
        "3. Я сгенерирую код и покажу превью\n"
        "4. Скажи «задеплой» или жми «✅ Готово»\n"
        "5. Скачай код или задеплой в свой GitHub\n\n"
        "<b>Чтобы сайт открывался по ссылке у друзей:</b>\n"
        "1. Подключи GitHub (кнопка «🐙 GitHub» в меню)\n"
        "2. Сгенерируй сайт\n"
        "3. В превью жми «🐙 Залить в GitHub»\n"
        "4. Включи Pages в репо (Settings → Pages → GitHub Actions)\n"
        "5. Готово — ссылка работает для всех\n\n"
        "<b>Хостинг:</b>\n"
        "• GitHub Pages (бесплатно, твой репо)\n"
        "• Layero (бесплатно, РФ, HTTPS из коробки)\n\n"
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
    """Inline button 'Создать сайт' — runs single-prompt /site flow.

    Sends ONE message: scene PNG + prompt instructions + inline buttons.
    """
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)

    # Lazy import to avoid circular dependency
    from bot.handlers.site_builder import SiteFlow

    await state.clear()
    await state.set_state(SiteFlow.waiting_for_prompt)

    caption = (
        "✦ <b>Создаём новый сайт</b>\n\n"
        "Опиши всё в одном сообщении — я разберу сам что нужно. "
        "Можно упомянуть:\n\n"
        "• <i>Что за бизнес/проект</i>\n"
        "• <i>Стиль</i> (минимализм, яркий, строгий, журнальный)\n"
        "• <i>Цвета</i> (тёмная, тёплая, синяя, серая)\n"
        "• <i>Секции</i> (меню, услуги, портфолио, контакты)\n"
        "• <i>Что должна делать кнопка</i>\n\n"
        "Пример:\n"
        "<i>«Кофейня Brew в центре Москвы, тёплый минимализм, "
        "тёмно-коричневая палитра, секции hero/меню/контакты, "
        "кнопка «Записаться»»</i>"
    )
    await _send_scene_with_text(
        msg,
        "generating",
        caption,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📋 В меню", callback_data="sb:menu")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:sites")
async def cb_menu_sites(callback: CallbackQuery) -> None:
    """Inline button 'Мои сайты' — calls /sites logic.

    ONE message per state (with or without image, depending on result).
    """
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
            await _send_scene_with_text(
                msg,
                "error",
                "📦 <b>Мои сайты</b>\n\n<i>БД недоступна.</i>",
            )
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
            await _send_scene_with_text(
                msg,
                "no_sites",
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
        await _send_scene_with_text(
            msg,
            "menu",
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
        await _send_scene_with_text(msg, "error", f"✗ Ошибка: <code>{exc}</code>")
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
    """Inline button 'Рефералы' — runs /referral logic.

    ONE message: referral scene + caption + share button.
    """
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

    caption = (
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
    await _send_scene_with_text(
        msg,
        "referral",
        caption,
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
            "Это всё управляется кнопками:\n"
            "• <b>Создать сайт</b> — пройди 7-шаговый бриф\n"
            "• <b>Мои сайты</b> — список твоих сайтов\n"
            "• <b>SEO-статьи</b> — статейный движок (в разработке)\n"
            "• <b>Рефералы</b> — зарабатывай 30% с платежей\n\n"
            "Вопросы — пиши прямо сюда в чат.",
            reply_markup=_main_keyboard(),
        )
    except Exception:  # noqa: BLE001
        pass
    await callback.answer()


@router.callback_query(F.data == "menu:github")
async def cb_menu_github(callback: CallbackQuery, state: FSMContext) -> None:
    """Inline button 'GitHub' — runs /github logic."""
    if callback.message is None:
        await callback.answer()
        return
    # Имитируем /github: создаём новое сообщение с тем же текстом
    from bot.handlers.auth_github import cmd_github

    msg = cast(Message, callback.message)
    # Подменяем message.text чтобы cmd_github сработал
    fake = cast(Message, msg.model_copy(update={"text": "/github"}))
    await cmd_github(fake, state)
    await callback.answer()


# ============== Fallback ==============


@router.message()
async def fallback(message: Message) -> None:
    """Catch-all for unrecognized messages — guide user to buttons."""
    if message.text and message.text.startswith("/"):
        return
    await message.answer(
        "✦ Это пока вне моего скоупа.\n\n"
        "Попробуй:\n"
        "• Жми «✦ Создать сайт» в меню\n"
        "• Опиши задачу словами: <i>«сделай лендинг для кофейни»</i>"
    )
