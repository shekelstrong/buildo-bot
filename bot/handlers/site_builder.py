"""/site — site-builder flow with preview, agent, and time-travel.

Flow:
  /site          -> ask prompt
  [prompt]       -> generate via LLM
                 -> auto-deploy preview
                 -> show URL + inline buttons [Исправить / Готово / Удалить / Версии]
  [edit]         -> in 'editing' state: agent applies change, re-deploys
                 -> shows diff summary + new URL + "В меню" button
  [Done button]  -> publish (mark as deployed in DB)
  [Menu button]  -> exit editing, back to main menu
  /cancel        -> abort, clean up

Time travel: every deploy creates v1, v2, v3... User can roll back via /versions or [Версии] button
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from typing import cast

from bot.services import (
    database,
    github_export,
    preview,
    quality,
    site_generator,
    supabase,
)
from bot.services.brief import enrich_prompt
from bot.services.agent import apply_edit
from bot.services.scenes import get_scene

logger = logging.getLogger(__name__)

router = Router(name="site_builder")


class SiteFlow(StatesGroup):
    """FSM for site creation + dialog editing flow.

    Single-prompt flow:
    1. waiting_for_prompt — юзер отправил текст, идёт генерация
    → generating → preview / editing
    """

    waiting_for_prompt = State()
    generating = State()
    preview = State()
    editing = State()


# Callback data (max 64 bytes per Telegram spec)
CB_EDIT = "sb:edit"
CB_DONE = "sb:done"
CB_DELETE = "sb:delete"
CB_MENU = "sb:menu"
CB_VERSIONS = "sb:versions"
CB_RETRY = "sb:retry"
CB_ROLLBACK_PREFIX = "sb:rb:"
CB_DOWNLOAD = "sb:dl"
CB_GITHUB = "sb:gh"
CB_PAGES = "sb:pg"


def _preview_keyboard() -> InlineKeyboardMarkup:
    """Inline buttons for site-preview state — exposes deploy + download."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Исправить", callback_data=CB_EDIT),
                InlineKeyboardButton(text="✅ Готово", callback_data=CB_DONE),
            ],
            [
                InlineKeyboardButton(text="🐙 GitHub", callback_data=CB_GITHUB),
                InlineKeyboardButton(text="🌐 Pages", callback_data=CB_PAGES),
            ],
            [
                InlineKeyboardButton(text="📦 Скачать код", callback_data=CB_DOWNLOAD),
                InlineKeyboardButton(text="🕒 Версии", callback_data=CB_VERSIONS),
            ],
            [
                InlineKeyboardButton(text="🗑 Удалить", callback_data=CB_DELETE),
                InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU),
            ],
        ]
    )


def _editing_keyboard() -> InlineKeyboardMarkup:
    """Inline buttons for editing state — always shows 'В меню'."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Готово", callback_data=CB_DONE),
                InlineKeyboardButton(text="🕒 Версии", callback_data=CB_VERSIONS),
            ],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=CB_DELETE)],
            [InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)],
        ]
    )


async def _send_scene(message: Message, scene_name: str) -> None:
    """Send a scene PNG inline (Telegram renders as photo)."""
    try:
        png = get_scene(scene_name)
        await message.answer_photo(
            photo=BufferedInputFile(png, filename=f"{scene_name}.png"),
            caption="",
        )
    except Exception:  # noqa: BLE001
        logger.exception("scene %s send failed", scene_name)


def _versions_keyboard(
    versions: list[dict], current_version: str
) -> InlineKeyboardMarkup:
    """Inline buttons for Time Travel — one per version."""
    rows = []
    for v in versions[:8]:  # max 8 buttons (Telegram limit)
        ver = v.get("version", "?")
        marker = " ←" if ver == current_version else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"⏪ {ver}{marker}", callback_data=f"{CB_ROLLBACK_PREFIX}{ver}"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("site"))
async def cmd_site(message: Message, state: FSMContext) -> None:
    """Start site-builder flow. Один промт — несколько внутренних шагов."""
    await state.clear()
    await state.set_state(SiteFlow.waiting_for_prompt)

    try:
        from bot.services.scenes import get_scene

        png = get_scene("generating")
        await message.answer_photo(
            photo=BufferedInputFile(png, filename="generating.png"),
            caption="",
        )
    except Exception:  # noqa: BLE001
        pass

    await message.answer(
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
        "кнопка «Записаться»»</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)],
            ]
        ),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Cancel current flow."""
    await state.clear()
    await message.answer("✦ Отменил. Жми кнопки в меню.")


# ============== receive_prompt — single-prompt with progress message ==============


@router.message(SiteFlow.waiting_for_prompt)
async def receive_prompt(message: Message, state: FSMContext) -> None:
    """User sent a prompt: parse → generate → save → deploy → preview, with progress."""
    if not message.text or not message.text.strip():
        await message.answer("Пришли текстом описание сайта.")
        return

    prompt = message.text.strip()
    tg_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id

    await state.set_state(SiteFlow.generating)
    await state.update_data(prompt=prompt)

    # ===== ProgressMessage — одно сообщение которое обновляется =====
    from bot.services.progress_message import ProgressMessage

    progress = ProgressMessage(message.bot, chat_id)
    await progress.start(
        "🔍 <b>Анализирую запрос…</b>\n\n"
        f"<tg-spoiler>{prompt[:200]}{'…' if len(prompt) > 200 else ''}</tg-spoiler>"
    )

    # ===== Этап 1: парсинг и обогащение =====
    await asyncio.sleep(0.4)
    enriched = enrich_prompt(prompt)
    await progress.update(
        "🎨 <b>Подбираю стиль и палитру…</b>\n\n"
        f"• Ниша: <i>{enriched['niche'][:60]}</i>\n"
        f"• Стиль: <i>{enriched['style_name']}</i>\n"
        f"• Палитра: <i>{enriched['palette_name']}</i>\n"
        f"• Секции: <i>{', '.join(enriched['sections'])}</i>\n"
        f"• CTA: <i>{enriched['cta_label']}</i>"
    )

    # ===== Этап 2: подготовка промта =====
    await asyncio.sleep(0.3)
    full_prompt = enriched["to_prompt"]()
    await progress.update(
        "🏗 <b>Строю структуру сайта…</b>\n\n"
        f"• HTML-структура: <i>{len(enriched['sections'])} секций</i>\n"
        f"• CSS-переменные: <i>палитра {enriched['palette_name']}</i>\n"
        f"• JS-интерактив: <i>hover, scroll, transitions</i>\n\n"
        "Готовлю детальный промт для модели…"
    )

    # ===== Этап 3: реальная LLM-генерация =====
    await asyncio.sleep(0.3)
    await progress.update(
        "✨ <b>Генерирую HTML/CSS/JS…</b>\n\n" "Модель пишет код. Обычно 15–40 секунд."
    )

    try:
        site = await site_generator.generate_site(prompt=full_prompt)
        if not site.files:
            await progress.fail("LLM не вернул файлы. Попробуй переформулировать.")
            await state.clear()
            return
        html_file = next(
            (f for f in site.files if f.path.endswith(".html")),
            site.files[0],
        )
        html_content = html_file.content
    except Exception as exc:  # noqa: BLE001
        logger.exception("generation failed")
        # Escape HTML so error snippets containing <!doctype> etc. don't
        # break Telegram's parse_mode='HTML' in the message that follows.
        from html import escape as _html_escape

        await progress.fail(f"Ошибка генерации: {_html_escape(str(exc)[:200])}")
        await state.clear()
        return

    # ===== Этап 4: проверка качества =====
    await progress.update("🔍 <b>Проверяю качество…</b>\n\nОцениваю по 7 критериям…")
    await asyncio.sleep(0.3)
    try:
        score = quality.score_site(html_content)
        quality_text = quality.format_score_for_user(score)
        score_line = f"<b>{score.overall}/10</b>"
    except Exception:  # noqa: BLE001
        logger.exception("quality scoring failed")
        quality_text = ""
        score_line = "—"
        score = None

    # ===== Этап 5: сохранение в БД =====
    await progress.update(
        "💾 <b>Сохраняю в базу…</b>\n\n"
        f"Качество: {score_line}\n"
        f"Размер: <i>{len(html_content) // 1024}KB</i>"
    )
    site_id = await database.save_site_from_html(
        tg_user_id=tg_id,
        name=enriched["niche"][:80],
        prompt=full_prompt,
        html_content=html_content,
    )
    if not site_id:
        await progress.fail("Не удалось сохранить сайт. Попробуй ещё раз.")
        await state.clear()
        return

    # ===== Этап 6: деплой превью =====
    await progress.update("📦 <b>Деплою превью…</b>\n\nКопирую в public dir…")
    files_for_preview = [{"path": "index.html", "content": html_content}]
    preview_result = await preview.deploy_preview(
        tg_user_id=tg_id,
        site_id=site_id,
        files=files_for_preview,
        project_name=enriched["niche"][:80],
    )
    if not preview_result.success:
        await progress.fail(f"Деплой упал: {preview_result.error[:200]}")
        await state.clear()
        return
    preview_url = preview_result.url

    # Save version v1
    try:
        await preview.save_version(tg_id, site_id, "v1", files_for_preview)
    except Exception:  # noqa: BLE001
        logger.exception("save_version v1 failed")

    # ===== Готово — обновляем state + показываем финальный экран =====
    await state.set_state(SiteFlow.preview)
    await state.update_data(
        site_id=site_id,
        preview_url=preview_url,
        current_files=files_for_preview,
        current_version="v1",
        project_name=enriched["niche"][:80],
    )

    # Качество → кнопка
    quality_buttons: list[list[InlineKeyboardButton]] = []
    if score is not None and score.overall < 6.5:
        quality_buttons.append(
            [
                InlineKeyboardButton(
                    text="🔄 Перегенерировать",
                    callback_data=CB_RETRY,
                )
            ]
        )

    buttons = quality_buttons + [
        [InlineKeyboardButton(text="✏️ Исправить", callback_data=CB_EDIT)],
        [
            InlineKeyboardButton(text="🐙 GitHub", callback_data=CB_GITHUB),
            InlineKeyboardButton(text="🌐 Pages", callback_data=CB_PAGES),
        ],
        [
            InlineKeyboardButton(text="📦 Скачать код", callback_data=CB_DOWNLOAD),
            InlineKeyboardButton(text="🕒 Версии", callback_data=CB_VERSIONS),
        ],
        [InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)],
    ]

    # Делаем превью-ссылку короткой и кликабельной: отрезаем хост/порт
    short_preview = preview_url
    if "://" in short_preview:
        short_preview = short_preview.split("://", 1)[1]
    if short_preview.startswith("108.165.164.85:9090/"):
        short_preview = short_preview.replace("108.165.164.85:9090/", "buildo/", 1)

    await progress.finish(
        f"✦ <b>Готово!</b>\n\n"
        f'🌐 <b>Превью</b>: <a href="{preview_url}">{short_preview}</a>\n\n'
        f"{quality_text}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == CB_MENU)
async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Exit to main menu (cancel current site flow)."""
    await state.clear()
    if callback.message:
        try:
            await cast(Message, callback.message).edit_text(
                "✦ <b>Главное меню</b>\n\n" "Жми кнопки ниже чтобы продолжить."
            )
        except Exception:  # noqa: BLE001
            await cast(Message, callback.message).answer(
                "✦ <b>Главное меню</b>\n\n" "Жми кнопки ниже чтобы продолжить."
            )
    await callback.answer("Вернулся в меню")


@router.callback_query(F.data == CB_DONE)
async def cb_done(callback: CallbackQuery, state: FSMContext) -> None:
    """Finish editing and publish site as final."""
    data = await state.get_data()
    site_id = data.get("site_id")
    tg_id = callback.from_user.id if callback.from_user else None
    if site_id and tg_id:
        try:
            await database.update_site_status(
                site_id, "published", deploy_url=data.get("preview_url", "")
            )
        except Exception:  # noqa: BLE001
            logger.exception("publish failed")
    await state.clear()
    if callback.message:
        try:
            msg = cast(Message, callback.message)
            # Send scene
            try:
                png = get_scene("published")
                await msg.answer_photo(
                    photo=BufferedInputFile(png, filename="published.png"),
                    caption="",
                )
            except Exception:  # noqa: BLE001
                pass
            await msg.answer(
                "✦ <b>Готово!</b>\n\n"
                f"Сайт опубликован: {data.get('preview_url', '—')}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📦 Скачать код", callback_data="site:download"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="📋 В меню", callback_data="menu:home"
                            )
                        ],
                    ]
                ),
            )
        except Exception:  # noqa: BLE001
            pass
    await callback.answer("Опубликовано!")


@router.callback_query(F.data == CB_DELETE)
async def cb_delete(callback: CallbackQuery, state: FSMContext) -> None:
    """Delete current site from disk and DB."""
    data = await state.get_data()
    site_id = data.get("site_id")
    tg_id = callback.from_user.id if callback.from_user else 0
    if site_id:
        try:
            await database.delete_site(site_id)
        except Exception:  # noqa: BLE001
            logger.exception("delete_site failed")
        # Also remove from disk
        try:
            import shutil
            from pathlib import Path

            site_path = Path.home() / "buildo-sites" / str(tg_id) / site_id
            if site_path.exists():
                shutil.rmtree(site_path, ignore_errors=True)
            public_path = Path.home() / "buildo-sites" / "public" / str(tg_id) / site_id
            if public_path.exists():
                shutil.rmtree(public_path, ignore_errors=True)
        except Exception:  # noqa: BLE001
            logger.exception("disk cleanup failed")
    await state.clear()
    if callback.message:
        try:
            await cast(Message, callback.message).edit_text(
                "🗑 <b>Удалено.</b>",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📋 В меню", callback_data="menu:home"
                            )
                        ]
                    ]
                ),
            )
        except Exception:  # noqa: BLE001
            pass
    await callback.answer("Удалено")


@router.callback_query(F.data == CB_EDIT)
async def cb_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Switch from preview to editing mode."""
    await state.set_state(SiteFlow.editing)
    if callback.message:
        try:
            await cast(Message, callback.message).edit_text(
                "✏️ <b>Режим правок</b>\n\n"
                "Пиши что изменить — я переделаю.\n"
                "Например: <i>«поменяй hero на тёмный»</i>, <i>«добавь секцию с ценами»</i>, "
                "<i>«сделай логотип больше»</i>\n\n"
                "Каждое изменение = новая версия. Можно откатиться через 🕒 Версии.",
                reply_markup=_editing_keyboard(),
            )
        except Exception:  # noqa: BLE001
            pass
    await callback.answer("Можно писать правки")


@router.callback_query(F.data == CB_VERSIONS)
async def cb_versions(callback: CallbackQuery, state: FSMContext) -> None:
    """List saved versions with rollback buttons."""
    data = await state.get_data()
    site_id = data.get("site_id")
    tg_id = callback.from_user.id if callback.from_user else 0
    if not site_id:
        await callback.answer("Нет активного сайта", show_alert=True)
        return
    versions = preview.list_versions(tg_id, site_id)
    if not versions:
        await callback.answer("Версий пока нет", show_alert=True)
        return
    lines = ["🕒 <b>Версии сайта:</b>\n"]
    for v in versions:
        saved = v.get("saved_at", "?")[:19]
        marker = " ← текущая" if v.get("version") == data.get("current_version") else ""
        lines.append(
            f"• <b>{v.get('version', '?')}</b> · {v.get('files_count', 0)} файлов · {saved}{marker}"
        )
    lines.append("\nНажми кнопку ниже, чтобы откатиться.")
    if callback.message:
        try:
            await cast(Message, callback.message).edit_text(
                "\n".join(lines),
                reply_markup=_versions_keyboard(
                    versions, data.get("current_version", "")
                ),
            )
        except Exception:  # noqa: BLE001
            pass
    await callback.answer()


@router.callback_query(F.data.startswith(CB_ROLLBACK_PREFIX))
async def cb_rollback(callback: CallbackQuery, state: FSMContext) -> None:
    """Roll back to a specific version."""
    target_version = callback.data.split(":", 2)[2] if callback.data else ""
    data = await state.get_data()
    site_id = data.get("site_id")
    tg_id = callback.from_user.id if callback.from_user else 0
    if not site_id or not target_version:
        await callback.answer("Ошибка", show_alert=True)
        return

    files = preview.get_version_files(tg_id, site_id, target_version)
    if not files:
        await callback.answer(f"Версия {target_version} не найдена", show_alert=True)
        return

    result = await preview.deploy_preview(
        tg_id, site_id, files, data.get("project_name", "")
    )
    if not result.success:
        await callback.answer(f"Деплой упал: {result.error[:80]}", show_alert=True)
        return

    await state.update_data(
        current_version=target_version,
        current_files=files,
        preview_url=result.url,
    )
    if callback.message:
        try:
            await cast(Message, callback.message).edit_text(
                f"⏪ <b>Откатился к {target_version}</b>\n\n"
                f"🌐 Превью: {result.url}",
                reply_markup=_editing_keyboard(),
            )
        except Exception:  # noqa: BLE001
            pass
    await callback.answer(f"Версия {target_version}")


@router.message(Command("done"))
async def cmd_done(message: Message, state: FSMContext) -> None:
    """Same as CB_DONE but via command."""
    cur = await state.get_state()
    if cur != SiteFlow.editing.state:
        return
    data = await state.get_data()
    site_id = data.get("site_id")
    tg_id = message.from_user.id if message.from_user else None
    if site_id and tg_id:
        try:
            await database.update_site_status(
                site_id, "published", deploy_url=data.get("preview_url", "")
            )
        except Exception:  # noqa: BLE001
            logger.exception("publish failed")
    await state.clear()
    await message.answer(
        f"✦ <b>Готово!</b>\n\nСайт опубликован: {data.get('preview_url', '—')}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📦 Скачать код", callback_data="site:download"
                    )
                ],
                [InlineKeyboardButton(text="📋 В меню", callback_data="menu:home")],
            ]
        ),
    )


@router.message(SiteFlow.preview, ~F.text.startswith("/"))
async def receive_first_edit(message: Message, state: FSMContext) -> None:
    """After preview, user can send an edit instruction (text in dialog).

    Commands (starting with /) are NOT processed here — they fall through
    to common_handlers for /start, /help, /menu etc.
    """
    if message.text is None or not message.text.strip():
        return
    # Transition to editing state and process
    await state.set_state(SiteFlow.editing)
    await _apply_user_edit(message, state)


@router.message(SiteFlow.editing, ~F.text.startswith("/"))
async def receive_edit(message: Message, state: FSMContext) -> None:
    """In editing mode: apply user's instruction, re-deploy.

    Commands (starting with /) are NOT processed here — they fall through
    to common_handlers for /start, /help, /menu etc.
    """
    if message.text is None or not message.text.strip():
        return
    await _apply_user_edit(message, state)


async def _apply_user_edit(message: Message, state: FSMContext) -> None:
    """Common edit handler: agent edit -> save version -> re-deploy preview."""
    if message.text is None:
        return
    instruction = message.text.strip()
    data = await state.get_data()
    site_id = data.get("site_id")
    tg_id = message.from_user.id if message.from_user else 0
    current_files: list[dict[str, str]] = data.get("current_files", [])

    if not site_id or not current_files:
        await message.answer(
            "✗ Не нашёл текущий сайт в памяти. Начни заново: /site",
            reply_markup=None,
        )
        await state.clear()
        return

    thinking = await message.answer(f"✏️ <b>Применяю:</b> <i>{instruction[:200]}</i>")

    try:
        edit = await apply_edit(current_files, instruction)
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent edit failed")
        await thinking.edit_text(
            f"✗ Не получилось: <code>{exc}</code>\n\nПопробуй переформулировать.",
            reply_markup=_editing_keyboard(),
        )
        return

    # Save next version
    next_v = preview.next_version(tg_id, site_id)
    new_files_dicts = [{"path": f.path, "content": f.content} for f in edit.new_files]
    try:
        await preview.save_version(tg_id, site_id, next_v, new_files_dicts)
    except Exception:  # noqa: BLE001
        logger.exception("save_version failed")

    # Re-deploy preview
    result = await preview.deploy_preview(
        tg_id, site_id, new_files_dicts, data.get("project_name", "")
    )

    if not result.success:
        await thinking.edit_text(
            f"✗ Edit применился, но деплой упал: <code>{result.error[:200]}</code>\n\n"
            "Попробуй ещё раз.",
            reply_markup=_editing_keyboard(),
        )
        return

    # Update DB
    try:
        await database.update_site_deploy(site_id, deploy_url=result.url)
    except Exception:  # noqa: BLE001
        logger.exception("update deploy url failed")

    # Update state
    await state.update_data(
        current_version=next_v,
        current_files=new_files_dicts,
        preview_url=result.url,
    )

    await thinking.edit_text(
        f"✦ <b>{edit.preview_message}</b>\n\n"
        f"🌐 Новое превью: {result.url}\n\n"
        f"📝 {edit.summary}\n\n"
        "Ещё что-то поменять? Пиши.",
        reply_markup=_editing_keyboard(),
    )


@router.message(Command("versions"))
async def cmd_versions(message: Message, state: FSMContext) -> None:
    """List saved versions of current site (Time Travel)."""
    data = await state.get_data()
    site_id = data.get("site_id")
    tg_id = message.from_user.id if message.from_user else 0
    if not site_id:
        await message.answer("Нет активного сайта. /site — создать.")
        return
    versions = preview.list_versions(tg_id, site_id)
    if not versions:
        await message.answer("Версий пока нет.")
        return
    lines = ["🕒 <b>Версии сайта:</b>\n"]
    for v in versions:
        saved = v.get("saved_at", "?")[:19]
        marker = " ← текущая" if v.get("version") == data.get("current_version") else ""
        lines.append(
            f"• <b>{v.get('version', '?')}</b> · {v.get('files_count', 0)} файлов · {saved}{marker}"
        )
    lines.append("\n/rollback v2 — откатиться к версии (например)")
    await message.answer(
        "\n".join(lines),
        reply_markup=_versions_keyboard(versions, data.get("current_version", "")),
    )


@router.message(Command("rollback"))
async def cmd_rollback(message: Message, state: FSMContext) -> None:
    """Roll back to a specific version: /rollback v2"""
    if message.text is None:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Укажи версию: /rollback v2")
        return
    target_version = parts[1].strip()
    data = await state.get_data()
    site_id = data.get("site_id")
    tg_id = message.from_user.id if message.from_user else 0
    if not site_id:
        await message.answer("Нет активного сайта.")
        return

    files = preview.get_version_files(tg_id, site_id, target_version)
    if not files:
        await message.answer(f"✗ Версия {target_version} не найдена")
        return

    result = await preview.deploy_preview(
        tg_id, site_id, files, data.get("project_name", "")
    )
    if not result.success:
        await message.answer(f"✗ Деплой упал: {result.error[:200]}")
        return

    await state.update_data(
        current_version=target_version,
        current_files=files,
        preview_url=result.url,
    )
    await message.answer(
        f"⏪ Откатился к <b>{target_version}</b>\n\n🌐 Превью: {result.url}",
        reply_markup=_editing_keyboard(),
    )


@router.message(Command("sites"))
async def cmd_sites(message: Message) -> None:
    """List user's sites from PostgreSQL with scene + refresh button."""
    if message.from_user is None:
        return
    tg_user_id = message.from_user.id
    try:
        user = await supabase.upsert_tg_user(tg_user_id=tg_user_id)
        if user is None:
            await _send_scene(message, "error")
            await message.answer("📦 <b>Мои сайты</b>\n\n<i>БД недоступна.</i>")
            return
        user_id_raw = user.get("id") if isinstance(user, dict) else user["id"]
        if user_id_raw is None:
            await message.answer("✗ Ошибка user_id")
            return
        user_id = int(user_id_raw)
        sites = await supabase.list_user_sites(user_id, limit=20)
        if not sites:
            await _send_scene(message, "no_sites")
            await message.answer(
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
            return
        await _send_scene(message, "menu")
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
        lines.append("\n")
        await message.answer(
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
                    ],
                ]
            ),
        )
    except Exception as exc:
        logger.exception("cmd_sites failed")
        await _send_scene(message, "error")
        await message.answer(f"✗ Ошибка: <code>{exc}</code>")


@router.message(Command("static"))
async def cmd_static_info(message: Message) -> None:
    """Show how to access static sites."""
    await message.answer(
        "📁 <b>Статические превью</b>\n\n"
        "Каждый сгенерированный сайт доступен по URL:\n"
        "<code>http://108.165.164.85:9090/sites-static/&lt;tg_id&gt;/&lt;site_id&gt;/</code>\n\n"
        "URL приходит в сообщении после генерации."
    )


# ===================================================================
# Download / GitHub / Pages callbacks
# ===================================================================


async def _zip_files_for_download(
    tg_user_id: int, site_id: str, files: list[dict[str, str]]
) -> bytes:
    """Build a zip archive in memory of all generated files."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f["path"], f["content"])
        # Add README with usage info
        readme = (
            f"# Buildo site {site_id}\n\n"
            f"Generated by Buildo bot for tg_user_id={tg_user_id}.\n\n"
            f"## How to use\n\n"
            f"1. Extract this zip\n"
            f"2. Open `index.html` in a browser — done!\n"
            f"3. To deploy:\n"
            f"   - GitHub Pages: push to a repo, enable Pages, done\n"
            f"   - Vercel/Netlify: drag & drop the folder\n"
            f"   - Layero: `npx layero deploy --name {site_id[:8]}`\n\n"
            f"## Files\n\n"
        )
        for f in files:
            readme += f"- `{f['path']}` ({len(f['content'])} bytes)\n"
        zf.writestr("README.md", readme)
    return buf.getvalue()


@router.callback_query(F.data == CB_DOWNLOAD)
async def cb_download(callback: CallbackQuery, state: FSMContext) -> None:
    """Download the current site as a zip archive."""
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    data = await state.get_data()
    files = data.get("current_files")
    site_id = data.get("site_id")
    tg_id = callback.from_user.id if callback.from_user else 0
    if not files or not site_id:
        await callback.answer("Нет активного сайта", show_alert=True)
        return

    await callback.answer("Готовлю архив...")
    try:
        zip_bytes = await _zip_files_for_download(tg_id, site_id, files)
        await msg.answer_document(
            document=BufferedInputFile(zip_bytes, filename=f"buildo-{site_id[:8]}.zip"),
            caption=(
                f"📦 <b>Код сайта</b>\n\n"
                f"Файлов: {len(files)}\n"
                f"Размер архива: {len(zip_bytes) // 1024}KB\n\n"
                f"Распакуй и открой <code>index.html</code> в браузере."
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("cb_download failed")
        await msg.answer(f"✗ Ошибка: <code>{exc}</code>")


@router.callback_query(F.data == CB_GITHUB)
async def cb_github(callback: CallbackQuery, state: FSMContext) -> None:
    """Push current site to GitHub (user's own repo if connected, else fallback).

    If user not connected to GitHub — show CTA to /github connect instead
    of silently pushing to private shekelstrong/buildo-sites (which 404s).
    """
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    data = await state.get_data()
    files = data.get("current_files")
    site_id = data.get("site_id")
    tg_id = callback.from_user.id if callback.from_user else 0
    project_name = data.get("project_name", site_id or "buildo-site")
    if not files or not site_id:
        await callback.answer("Нет активного сайта", show_alert=True)
        return

    # Проверяем, подключён ли юзер к GitHub
    gh_info = await database.get_user_github_info(tg_id)
    user_connected = gh_info and gh_info.get("connected")

    if not user_connected:
        # CTA: подключи GitHub — тогда сайт улетит в твой репо
        from bot.handlers.auth_github import _github_disconnected_kb

        await callback.answer()
        await msg.answer(
            "🐙 <b>GitHub не подключён</b>\n\n"
            "Сейчас бот зальёт сайт в <b>общий</b> приватный репозиторий "
            "shekelstrong/buildo-sites — оттуда его не расшаришь.\n\n"
            "Лучше подключи свой GitHub — это 15 секунд:\n"
            "• Кнопка ниже → <b>⚡ OAuth (рекомендую)</b>\n"
            "• Или отправь <code>/github connect</code>\n\n"
            "Тогда сайт зальётся в <code>твой_юзер/buildo-sites</code> "
            "и ты сможешь открыть его через GitHub Pages.",
            reply_markup=_github_disconnected_kb(),
        )
        return

    # User connected — push to user's repo
    await callback.answer("Пушу в твой GitHub...")
    username = gh_info["github_username"]
    try:
        user_token = await database.get_user_github_token(tg_id)
        if not user_token:
            await msg.answer(
                "⚠️ Token не найден. Попробуй /github disconnect → /github connect."
            )
            return
        token_enc, _ = user_token
        # Decrypt
        from bot.services.github_export import decrypt_token

        plain_token = decrypt_token(token_enc)

        result = await github_export.push_files_to_user_repo(
            github_token=plain_token,
            github_username=username,
            tg_user_id=tg_id,
            site_id=site_id,
            files=files,
            commit_message=f"buildo: {project_name} ({site_id[:8]})",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("github push (user repo) failed")
        await msg.answer(f"✗ Ошибка GitHub: <code>{exc}</code>")
        return

    if not result["success"]:
        await msg.answer(
            f"✗ <b>GitHub push не удался</b>\n\n"
            f"<code>{result.get('error', '')[:300]}</code>\n\n"
            f"Попробуй /github disconnect → /github connect заново."
        )
        return

    short_sha = result.get("commit_sha", "")[:7]
    pages_url = f"https://{username}.github.io/buildo-sites/sites/{tg_id}/{site_id}/"
    await msg.answer(
        f"✦ <b>Залито в твой GitHub!</b>\n\n"
        f"Аккаунт: <b>@{username}</b>\n"
        f"Файлов: <b>{result['files_pushed']}</b>\n"
        f"Commit: <code>{short_sha}</code>\n"
        f"🔗 <a href=\"{result['repo_url']}\">Открыть в GitHub</a>\n\n"
        f"🌐 <b>Чтобы открыть публично</b>:\n"
        f"1. Зайди в репо <code>{username}/buildo-sites</code>\n"
        f"2. Settings → Pages → Source: <b>GitHub Actions</b>\n"
        f"3. Подожди ~30 сек\n\n"
        f"Готовая ссылка (после публикации):\n"
        f"<a href=\"{pages_url}\">{pages_url}</a>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Открыть URL", url=pages_url)],
                [InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)],
            ]
        ),
    )


@router.callback_query(F.data == CB_PAGES)
async def cb_pages(callback: CallbackQuery, state: FSMContext) -> None:
    """Get a public GitHub Pages URL for the current site."""
    if callback.message is None:
        await callback.answer()
        return
    msg = cast(Message, callback.message)
    data = await state.get_data()
    site_id = data.get("site_id")
    tg_id = callback.from_user.id if callback.from_user else 0
    if not site_id:
        await callback.answer("Сначала сохрани сайт в GitHub", show_alert=True)
        return

    await callback.answer()
    try:
        result = await github_export.create_github_pages_deploy(tg_id, site_id)
        if not result.get("success"):
            await msg.answer(
                f"✗ Pages недоступны: <code>{result.get('error', '')}</code>"
            )
            return
        url = result["pages_url"]
        await msg.answer(
            f"🌐 <b>Публичный URL</b>\n\n"
            f"<code>{url}</code>\n\n"
            f"⏳ GitHub опубликует через ~30 секунд (Actions → pages build).\n"
            f"Потом страница будет доступна всем.\n\n"
            f"💡 Эту ссылку можно расшарить — она публичная.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔗 Открыть", url=url)],
                    [InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)],
                ]
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("cb_pages failed")
        await msg.answer(f"✗ Ошибка: <code>{exc}</code>")
