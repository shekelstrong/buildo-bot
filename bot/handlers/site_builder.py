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
from typing import Any, cast

from bot.services import database, github_export, preview, site_generator, supabase
from bot.services import brief, file_prompts, quality
from bot.services.agent import apply_edit
from bot.services.scenes import get_scene

logger = logging.getLogger(__name__)

router = Router(name="site_builder")


class SiteFlow(StatesGroup):
    """FSM for multi-step site creation + dialog editing flow.

    Brief steps:
    1. waiting_for_niche — ниша
    2. waiting_for_style — стиль (кнопки)
    3. waiting_for_sections — секции (toggle кнопки)
    4. waiting_for_palette — палитра (кнопки)
    5. waiting_for_cta — CTA (кнопки)
    6. waiting_for_hero — hero-текст
    7. waiting_for_file — файл с ТЗ (опционально)
    → generation → preview / editing
    """

    waiting_for_niche = State()
    waiting_for_style = State()
    waiting_for_sections = State()
    waiting_for_palette = State()
    waiting_for_cta = State()
    waiting_for_hero = State()
    waiting_for_file = State()
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
    """Start multi-step site-builder flow (7 шагов)."""
    await state.clear()
    await state.set_state(SiteFlow.waiting_for_niche)
    await state.update_data(brief_sections=[])  # пустой список секций

    await _send_scene(message, "generating")
    await message.answer(
        "✦ <b>Создаём новый сайт</b>\n\n"
        "<b>Шаг 1/7: Расскажи про бизнес/проект</b>\n\n"
        "Что это за сайт, для кого, какая цель? "
        "Чем конкретнее — тем точнее результат.\n\n"
        "Примеры:\n"
        "• <i>Кофейня «Brew» в центре Москвы</i>\n"
        "• <i>Портфолио веб-дизайнера, фрилансер</i>\n"
        "• <i>Студия йоги в Петербурге</i>\n"
        "• <i>Онлайн-курс по Python для начинающих</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⏭ Пропустить", callback_data="brief:niche:skip"
                    )
                ],
                [InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)],
            ]
        ),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Cancel current flow."""
    await state.clear()
    await message.answer("✦ Отменил. Жми кнопки в меню.")


# ============== Multi-step brief handlers ==============

CB_STYLE_PREFIX = "brief:style:"
CB_PALETTE_PREFIX = "brief:palette:"
CB_SECTION_PREFIX = "brief:sec:"
CB_CTA_PREFIX = "brief:cta:"


def _style_keyboard() -> InlineKeyboardMarkup:
    """Шаг 2: выбор стиля."""
    rows = []
    for key, info in brief.STYLES.items():
        rows.append(
            [
                InlineKeyboardButton(
                    text=info["name"], callback_data=f"{CB_STYLE_PREFIX}{key}"
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🎲 Случайный", callback_data=f"{CB_STYLE_PREFIX}random"
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _palette_keyboard() -> InlineKeyboardMarkup:
    """Шаг 4: выбор палитры."""
    rows = []
    for key, info in brief.PALETTES.items():
        rows.append(
            [
                InlineKeyboardButton(
                    text=info["name"], callback_data=f"{CB_PALETTE_PREFIX}{key}"
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🎲 Случайная", callback_data=f"{CB_PALETTE_PREFIX}random"
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _sections_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    """Шаг 3: секции (toggle)."""
    rows = []
    for key, label in brief.SECTIONS.items():
        marker = "✓" if key in selected else " "
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"[{marker}] {label}",
                    callback_data=f"{CB_SECTION_PREFIX}{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➡ Готово", callback_data="brief:sec:done")])
    rows.append([InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cta_keyboard() -> InlineKeyboardMarkup:
    """Шаг 5: CTA."""
    rows = []
    for key, label in brief.CTA_TEMPLATES.items():
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"{CB_CTA_PREFIX}{key}")]
        )
    rows.append([InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _go_to_step(callback: CallbackQuery, state: FSMContext, step: int) -> None:
    """Перейти к следующему шагу брифа."""
    msg = cast(Message, callback.message)
    if step == 2:
        await state.set_state(SiteFlow.waiting_for_style)
        await msg.answer(
            "<b>Шаг 2/7: Выбери стиль</b>\n\n" "Каждый стиль даёт свою атмосферу.",
            reply_markup=_style_keyboard(),
        )
    elif step == 3:
        data = await state.get_data()
        selected = data.get("brief_sections", [])
        await state.set_state(SiteFlow.waiting_for_sections)
        await msg.answer(
            "<b>Шаг 3/7: Какие секции нужны?</b>\n\n"
            "Нажми на секцию чтобы добавить/убрать.",
            reply_markup=_sections_keyboard(selected),
        )
    elif step == 4:
        await state.set_state(SiteFlow.waiting_for_palette)
        await msg.answer(
            "<b>Шаг 4/7: Выбери палитру</b>\n\n" "Это цвета твоего сайта.",
            reply_markup=_palette_keyboard(),
        )
    elif step == 5:
        await state.set_state(SiteFlow.waiting_for_cta)
        await msg.answer(
            "<b>Шаг 5/7: Главная кнопка</b>\n\n" "Что должна делать главная кнопка?",
            reply_markup=_cta_keyboard(),
        )
    elif step == 6:
        await state.set_state(SiteFlow.waiting_for_hero)
        await msg.answer(
            "<b>Шаг 6/7: Hero-текст</b>\n\n"
            "Напиши главный заголовок и подзаголовок для первого экрана. "
            "Через «|» раздели заголовок и подзаголовок.\n\n"
            "Пример: <i>Кофейня Brew | Уютное место в центре Москвы</i>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)]
                ]
            ),
        )


async def _generate_from_brief(
    source, state: FSMContext, brief_data: brief.BriefData
) -> None:
    """Сгенерировать сайт из собранного брифа."""
    tg_id = (
        source.from_user.id
        if hasattr(source, "from_user") and source.from_user
        else None
    )
    if tg_id is None:
        return

    if hasattr(source, "message") and source.message:
        msg = cast(Message, source.message)
    else:
        msg = cast(Message, source)

    await state.set_state(SiteFlow.generating)
    await state.update_data(brief_prompt=brief_data.to_prompt())

    await msg.answer(
        "✦ <b>Генерирую сайт…</b>\n\n"
        f"Бриф:\n<tg-spoiler>{brief_data.to_prompt()[:300]}…</tg-spoiler>\n\n"
        "Обычно 15–40 секунд."
    )

    try:
        site = await site_generator.generate_site(prompt=brief_data.to_prompt())
        # Извлекаем HTML из сгенерированного сайта
        if not site.files:
            await msg.answer("⚠️ LLM не вернул файлы. Попробуй ещё раз.")
            await state.clear()
            return
        # Берём index.html или первый html-файл
        html_file = next(
            (f for f in site.files if f.path.endswith(".html")),
            site.files[0],
        )
        html_content = html_file.content
    except Exception as exc:  # noqa: BLE001
        logger.exception("generation failed")
        await msg.answer(f"⚠️ Ошибка генерации: {str(exc)[:200]}")
        await state.clear()
        return

    # Save site
    site_id = await database.save_site_from_html(
        tg_user_id=tg_id,
        name=brief_data.niche or "Untitled",
        prompt=brief_data.to_prompt(),
        html_content=html_content,
    )
    if not site_id:
        await msg.answer("⚠️ Не удалось сохранить сайт. Попробуй ещё раз.")
        await state.clear()
        return

    # Deploy to preview
    files_for_preview = [{"path": "index.html", "content": html_content}]
    preview_result = await preview.deploy_preview(
        tg_user_id=tg_id,
        site_id=site_id,
        files=files_for_preview,
        project_name=brief_data.niche or "Untitled",
    )
    preview_url = preview_result.url

    # Quality scoring
    quality_buttons: list[list[InlineKeyboardButton]] = []
    quality_text = ""
    try:
        score = quality.score_site(html_content)
        quality_text = "\n\n" + quality.format_score_for_user(score)
        if score.overall < 6.5:
            quality_buttons.append(
                [
                    InlineKeyboardButton(
                        text="🔄 Перегенерировать",
                        callback_data=CB_RETRY,
                    )
                ]
            )
        else:
            quality_buttons.append(
                [
                    InlineKeyboardButton(
                        text="✅ Принять и опубликовать",
                        callback_data=CB_DONE,
                    )
                ]
            )
    except Exception:  # noqa: BLE001
        logger.exception("quality scoring failed")

    # Save version v1
    try:
        await preview.save_version(tg_id, site_id, "v1", files_for_preview)
    except Exception:  # noqa: BLE001
        logger.exception("save_version v1 failed")

    await state.set_state(SiteFlow.preview)
    await state.update_data(
        site_id=site_id,
        preview_url=preview_url,
        current_files=files_for_preview,
        current_version="v1",
        project_name=brief_data.niche or "Untitled",
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

    await msg.answer(
        f"✦ <b>Сайт готов!</b>\n\n"
        f"🌐 Превью: <code>{preview_url}</code>{quality_text}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ===== Step 1: Niche =====


@router.callback_query(F.data == "brief:niche:skip")
async def cb_brief_niche_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Пропустить нишу — пусть LLM придумает."""
    await state.update_data(brief_niche="универсальный лендинг")
    await callback.answer("Ок")
    await _go_to_step(callback, state, 2)


@router.message(SiteFlow.waiting_for_niche)
async def receive_niche(message: Message, state: FSMContext) -> None:
    """Получить нишу от юзера."""
    if not message.text:
        await message.answer("Пришли текстом, что за сайт.")
        return
    await state.update_data(brief_niche=message.text.strip())
    await message.answer(
        "✦ Принял: <b>" + message.text.strip()[:80] + "</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➡ Дальше", callback_data="brief:niche:next"
                    )
                ],
                [InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)],
            ]
        ),
    )


@router.callback_query(F.data == "brief:niche:next")
async def cb_brief_niche_next(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _go_to_step(callback, state, 2)


# ===== Step 2: Style =====


@router.callback_query(F.data.startswith(CB_STYLE_PREFIX))
async def cb_brief_style(callback: CallbackQuery, state: FSMContext) -> None:
    style_key = (callback.data or "").replace(CB_STYLE_PREFIX, "")
    if style_key == "random":
        import random

        style_key = random.choice(list(brief.STYLES.keys()))
    await state.update_data(brief_style=style_key)
    await callback.answer(f"Стиль: {brief.STYLES[style_key]['name']}")
    await _go_to_step(callback, state, 3)


# ===== Step 3: Sections =====


@router.callback_query(F.data.startswith(CB_SECTION_PREFIX))
async def cb_brief_section(callback: CallbackQuery, state: FSMContext) -> None:
    section = (callback.data or "").replace(CB_SECTION_PREFIX, "")
    if section == "done":
        data = await state.get_data()
        selected = data.get("brief_sections", [])
        if not selected:
            await callback.answer("Выбери хотя бы одну секцию", show_alert=True)
            return
        await callback.answer("Ок")
        await _go_to_step(callback, state, 4)
        return

    data = await state.get_data()
    selected: list[str] = list(data.get("brief_sections", []))
    if section in selected:
        selected.remove(section)
    else:
        selected.append(section)
    await state.update_data(brief_sections=selected)

    try:
        await cast(Message, callback.message).edit_reply_markup(
            reply_markup=_sections_keyboard(selected)
        )
    except Exception:  # noqa: BLE001
        pass
    await callback.answer(f"Секции: {len(selected)}")


# ===== Step 4: Palette =====


@router.callback_query(F.data.startswith(CB_PALETTE_PREFIX))
async def cb_brief_palette(callback: CallbackQuery, state: FSMContext) -> None:
    palette_key = (callback.data or "").replace(CB_PALETTE_PREFIX, "")
    if palette_key == "random":
        import random

        palette_key = random.choice(list(brief.PALETTES.keys()))
    await state.update_data(brief_palette=palette_key)
    await callback.answer(f"Палитра: {brief.PALETTES[palette_key]['name']}")
    await _go_to_step(callback, state, 5)


# ===== Step 5: CTA =====


@router.callback_query(F.data.startswith(CB_CTA_PREFIX))
async def cb_brief_cta(callback: CallbackQuery, state: FSMContext) -> None:
    cta_key = (callback.data or "").replace(CB_CTA_PREFIX, "")
    await state.update_data(brief_cta=cta_key)
    await callback.answer(f"CTA: {brief.CTA_TEMPLATES[cta_key]}")
    await _go_to_step(callback, state, 6)


# ===== Step 6: Hero =====


@router.message(SiteFlow.waiting_for_hero)
async def receive_hero(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пришли текстом hero-строку.")
        return
    await state.update_data(brief_hero=message.text.strip())
    await state.set_state(SiteFlow.waiting_for_file)
    await message.answer(
        "✦ Принял!\n\n"
        "<b>Шаг 7/7: Файл с ТЗ (опционально)</b>\n\n"
        "Если есть подробное ТЗ в .txt / .md / .pdf — прикрепи файл. "
        "Если нет — нажми «🚀 Генерировать».",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚀 Генерировать", callback_data="brief:gen"
                    )
                ],
                [InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)],
            ]
        ),
    )


# ===== Step 7: File (optional) =====


@router.message(SiteFlow.waiting_for_file, F.document)
async def receive_brief_file(message: Message, state: FSMContext) -> None:
    """Скачать файл и сохранить содержимое в бриф."""
    if not message.document:
        return
    try:
        text, filename = await file_prompts.download_and_extract(
            cast(Any, message.bot), message.document
        )
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    except Exception:  # noqa: BLE001
        logger.exception("file download failed")
        await message.answer("⚠️ Не удалось прочитать файл.")
        return

    await state.update_data(
        brief_extra_text=text,
        brief_extra_filename=filename,
    )
    await message.answer(
        f"✦ Файл <b>{filename}</b> принят ({len(text):,} символов).\n\n"
        "Жми «🚀 Генерировать».",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚀 Генерировать", callback_data="brief:gen"
                    )
                ],
                [InlineKeyboardButton(text="📋 В меню", callback_data=CB_MENU)],
            ]
        ),
    )


# ===== Generate from brief =====


@router.callback_query(F.data == "brief:gen")
async def cb_brief_generate(callback: CallbackQuery, state: FSMContext) -> None:
    """Собрать бриф и запустить генерацию."""
    data = await state.get_data()
    bd = brief.BriefData(
        user_id=callback.from_user.id if callback.from_user else 0,
        niche=data.get("brief_niche"),
        style=data.get("brief_style"),
        sections=data.get("brief_sections", []),
        palette=data.get("brief_palette"),
        cta=data.get("brief_cta"),
        hero_text=data.get("brief_hero"),
        extra_file_text=data.get("brief_extra_text"),
        extra_filename=data.get("brief_extra_filename"),
    )
    missing = bd.missing_fields()
    if missing:
        await callback.answer(f"Не хватает: {', '.join(missing)}", show_alert=True)
        return

    await callback.answer("Запускаю генерацию…")
    await _generate_from_brief(callback, state, bd)


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
    """Push current site to shekelstrong/buildo-sites on GitHub."""
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

    await callback.answer("Пушу в GitHub...")
    try:
        result = await github_export.push_files_to_repo(
            tg_user_id=tg_id,
            site_id=site_id,
            files=files,
            commit_message=f"buildo: {project_name} ({site_id[:8]})",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("github push failed")
        await msg.answer(f"✗ Ошибка GitHub: <code>{exc}</code>")
        return

    if not result["success"]:
        await msg.answer(
            f"✗ <b>GitHub push не удался</b>\n\n"
            f"<code>{result.get('error', '')[:300]}</code>\n\n"
            f"Попробуй позже или скачай код кнопкой «📦 Скачать код»."
        )
        return

    short_sha = result.get("commit_sha", "")[:7]
    await msg.answer(
        f"✦ <b>Залито в GitHub!</b>\n\n"
        f"Файлов: <b>{result['files_pushed']}</b>\n"
        f"Commit: <code>{short_sha}</code>\n"
        f"🔗 <a href=\"{result['repo_url']}\">Открыть в GitHub</a>\n\n"
        f"Сайт приватный (только в нашей организации shekelstrong).\n"
        f"Чтобы расшарить — нажми «🌐 Pages» для публичного URL.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Pages", callback_data=CB_PAGES)],
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
