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

import logging
import uuid

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
from typing import cast

from bot.services import database, preview, supabase
from bot.services.agent import apply_edit
from bot.services.site_generator import generate_site

logger = logging.getLogger(__name__)

router = Router(name="site_builder")


class SiteFlow(StatesGroup):
    """FSM for site creation + dialog editing flow."""

    waiting_for_prompt = State()
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


def _preview_keyboard() -> InlineKeyboardMarkup:
    """Inline buttons for site-preview state."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Исправить", callback_data=CB_EDIT),
                InlineKeyboardButton(text="✅ Готово", callback_data=CB_DONE),
            ],
            [
                InlineKeyboardButton(text="🕒 Версии", callback_data=CB_VERSIONS),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=CB_DELETE),
            ],
            [InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)],
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


def _versions_keyboard(versions: list[dict], current_version: str) -> InlineKeyboardMarkup:
    """Inline buttons for Time Travel — one per version."""
    rows = []
    for v in versions[:8]:  # max 8 buttons (Telegram limit)
        ver = v.get("version", "?")
        marker = " ←" if ver == current_version else ""
        rows.append(
            [InlineKeyboardButton(text=f"⏪ {ver}{marker}", callback_data=f"{CB_ROLLBACK_PREFIX}{ver}")]
        )
    rows.append([InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("site"))
async def cmd_site(message: Message, state: FSMContext) -> None:
    """Start the site-builder flow."""
    await state.clear()
    await state.set_state(SiteFlow.waiting_for_prompt)
    await message.answer(
        "✦ <b>Создаём новый сайт</b>\n\n"
        "Опиши словами, что нужно сделать. Чем подробнее — тем точнее результат.\n\n"
        "Примеры:\n"
        "<i>«лендинг для кофейни в центре Москвы, тёплый минимализм, "
        "секции: hero, меню, контакты»</i>\n"
        "<i>«портфолио веб-дизайнера с кейсами и контактами, тёмная тема»</i>\n"
        "<i>«сайт-визитка для автосервиса, серьёзный стиль, форма записи»</i>\n\n"
        "/cancel — отмена"
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Cancel current flow."""
    await state.clear()
    await message.answer("✦ Отменил. /site — начать заново.")


@router.callback_query(F.data == CB_MENU)
async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Exit to main menu (cancel current site flow)."""
    await state.clear()
    if callback.message:
        try:
            await cast(Message, callback.message).edit_text(
                "✦ <b>Главное меню</b>\n\n"
                "• /site — создать сайт\n"
                "• /sites — мои сайты\n"
                "• /help — помощь"
            )
        except Exception:  # noqa: BLE001
            await cast(Message, callback.message).answer(
                "✦ <b>Главное меню</b>\n\n"
                "• /site — создать сайт\n"
                "• /sites — мои сайты\n"
                "• /help — помощь"
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
            await cast(Message, callback.message).edit_text(
                "✦ <b>Готово!</b>\n\n"
                f"Сайт опубликован: {data.get('preview_url', '—')}\n\n"
                "/site — создать ещё\n"
                "/sites — мои сайты"
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
                "🗑 <b>Удалено.</b>\n\n/site — создать новый\n/sites — мои сайты"
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
                reply_markup=_versions_keyboard(versions, data.get("current_version", "")),
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
        f"✦ <b>Готово!</b>\n\nСайт опубликован: {data.get('preview_url', '—')}\n\n"
        "/site — создать ещё\n/sites — мои сайты"
    )


@router.message(SiteFlow.waiting_for_prompt)
async def receive_prompt(message: Message, state: FSMContext) -> None:
    """User sent a prompt: generate, deploy preview, show URL."""
    if message.text is None or not message.text.strip():
        await message.answer("Пришли текстом описание сайта.")
        return

    prompt = message.text.strip()
    thinking = await message.answer(
        f"✦ <b>Генерирую сайт...</b>\n\n<i>{prompt[:150]}{'...' if len(prompt) > 150 else ''}</i>\n\n"
        "Обычно 15-40 секунд."
    )

    try:
        site = await generate_site(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_site failed")
        await thinking.edit_text(
            f"✗ Не получилось: <code>{exc}</code>\n\n"
            "Попробуй переформулировать или /cancel."
        )
        await state.clear()
        return

    # Create site_id, save to DB, deploy preview
    tg_id = message.from_user.id if message.from_user else 0
    site_id = str(uuid.uuid4())

    # Persist to DB first (so we can rollback)
    try:
        if tg_id:
            user = await supabase.upsert_tg_user(tg_user_id=tg_id)
            user_id_raw = user.get("id") if isinstance(user, dict) else user["id"]
            if user_id_raw is not None:
                user_id = int(user_id_raw)
                await database.save_site(
                    user_id=user_id,
                    project_name=site.project_name,
                    framework=site.framework,
                    files_count=len(site.files),
                    size_kb=site.total_size_kb,
                    preview_summary=site.preview_summary,
                    deploy_target="self-host",
                    deploy_url="",
                    prompt=prompt,
                    site_id=site_id,
                )
    except Exception:  # noqa: BLE001
        logger.exception("save_site failed (continuing with deploy)")

    # Save v1
    files_dicts = [{"path": f.path, "content": f.content} for f in site.files]
    try:
        await preview.save_version(tg_id, site_id, "v1", files_dicts)
    except Exception:  # noqa: BLE001
        logger.exception("save_version v1 failed")

    # Deploy preview
    result = await preview.deploy_preview(
        tg_id, site_id, files_dicts, site.project_name
    )

    if not result.success:
        await thinking.edit_text(
            f"✗ Не получилось задеплоить: <code>{result.error[:200]}</code>\n\n"
            "/cancel — отменить"
        )
        await state.clear()
        return

    # Update DB with URL
    try:
        await database.update_site_deploy(site_id, deploy_url=result.url)
    except Exception:  # noqa: BLE001
        logger.exception("update deploy url failed")

    # Save state for editing
    await state.update_data(
        prompt=prompt,
        site_id=site_id,
        project_name=site.project_name,
        preview_url=result.url,
        current_version="v1",
        current_files=files_dicts,
    )
    await state.set_state(SiteFlow.preview)

    # Build files preview
    file_list = "\n".join(f"  📄 {f.path}" for f in site.files[:10])

    await thinking.edit_text(
        f"✦ <b>Готово!</b>\n\n"
        f"<i>{site.preview_summary}</i>\n\n"
        f"🌐 <b>Превью:</b> {result.url}\n\n"
        f"📁 Файлы ({len(site.files)}):\n{file_list}\n\n"
        "Что дальше?",
        reply_markup=_preview_keyboard(),
    )


@router.message(SiteFlow.preview)
async def receive_first_edit(message: Message, state: FSMContext) -> None:
    """After preview, user can send an edit instruction (text in dialog)."""
    if message.text is None or not message.text.strip():
        return
    # Transition to editing state and process
    await state.set_state(SiteFlow.editing)
    await _apply_user_edit(message, state)


@router.message(SiteFlow.editing)
async def receive_edit(message: Message, state: FSMContext) -> None:
    """In editing mode: apply user's instruction, re-deploy."""
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
    """List user's sites from PostgreSQL."""
    if message.from_user is None:
        return
    tg_user_id = message.from_user.id
    try:
        user = await supabase.upsert_tg_user(tg_user_id=tg_user_id)
        if user is None:
            await message.answer("📦 <b>Мои сайты</b>\n\n<i>БД недоступна.</i>")
            return
        user_id_raw = user.get("id") if isinstance(user, dict) else user["id"]
        if user_id_raw is None:
            await message.answer("✗ Ошибка user_id")
            return
        user_id = int(user_id_raw)
        sites = await supabase.list_user_sites(user_id, limit=20)
        if not sites:
            await message.answer(
                "📦 <b>Мои сайты</b>\n\n"
                "<i>У тебя пока нет сайтов. /site — создать первый.</i>"
            )
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
        lines.append("\n/site — создать ещё")
        await message.answer("\n".join(lines))
    except Exception as exc:
        logger.exception("cmd_sites failed")
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
