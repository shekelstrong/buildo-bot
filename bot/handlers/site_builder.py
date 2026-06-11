"""/site and /sites — site-builder flow (Phase 1 / MVP).

Full flow:
  /site  -> wait prompt -> generate via MiniMax M3
         -> preview summary + file list
         -> ask deploy target (Layero / GitHub)
         -> deploy -> send URL
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.services import github_export, layero, supabase
from bot.services.site_generator import generate_site

logger = logging.getLogger(__name__)

router = Router(name="site_builder")


class SiteFlow(StatesGroup):
    """FSM for site creation flow."""

    waiting_for_prompt = State()
    preview = State()
    waiting_for_deploy_target = State()


# Callback data constants (use prefixes to avoid collisions)
CB_DEPLOY_LAYERO = "site:deploy:layero"
CB_DEPLOY_GITHUB = "site:deploy:github"
CB_REGENERATE = "site:regenerate"
CB_CANCEL = "site:cancel"


def _build_files_preview(site) -> str:
    """Short file list for the preview message (no full code)."""
    lines = [f"📁 <b>{p}</b>" for p in sorted({f.path for f in site.files})]
    return "\n".join(lines[:12])  # cap at 12 for telegram message limit


@router.message(Command("site"))
async def cmd_site(message: Message, state: FSMContext) -> None:
    """Start the site-builder flow."""
    await state.set_state(SiteFlow.waiting_for_prompt)
    await message.answer(
        "🚀 <b>Создаём новый сайт</b>\n\n"
        "Опиши словами, что нужно сделать. Чем подробнее — тем точнее результат.\n\n"
        "Примеры:\n"
        "<i>«лендинг для кофейни в центре Москвы, тёплый минимализм, "
        "секции: hero, меню, контакты»</i>\n"
        "<i>«портфолио веб-дизайнера с кейсами и контактами, тёмная тема»</i>\n"
        "<i>«сайт-визитка для автосервиса, серьёзный стиль, форма записи»</i>\n\n"
        "Нажми /cancel для отмены."
    )


@router.message(Command("sites"))
async def cmd_sites(message: Message) -> None:
    """List user's sites (stub - Phase 1.5 will pull from Supabase)."""
    await message.answer(
        "📦 <b>Мои сайты</b>\n\n"
        "<i>Список появится после того как Supabase будет подключён. "
        "Сейчас все сгенерированные сайты ты можешь увидеть по "
        "ссылке которую я пришлю после генерации.</i>"
    )


@router.message(SiteFlow.waiting_for_prompt)
async def receive_prompt(message: Message, state: FSMContext) -> None:
    """Receive user's prompt, generate site, show preview."""
    if message.text is None or not message.text.strip():
        await message.answer("Пришли текстом описание сайта.")
        return

    prompt = message.text.strip()
    thinking = await message.answer(
        f"✅ Принял: <i>{prompt[:150]}{'...' if len(prompt) > 150 else ''}</i>\n\n"
        "🤖 Думаю над дизайном и кодом...\n"
        "<i>(обычно 30-90 секунд)</i>"
    )

    try:
        site = await generate_site(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.exception("site.gen failed")
        await thinking.edit_text(
            f"❌ Не получилось сгенерировать сайт: <code>{exc}</code>\n\n"
            "Попробуй переформулировать запрос или нажми /cancel."
        )
        await state.clear()
        return

    # Persist in FSM
    await state.update_data(
        prompt=prompt,
        site=site.to_dict(),
    )
    await state.set_state(SiteFlow.preview)

    files_preview = _build_files_preview(site)
    summary = site.preview_summary or "Готово."

    kb = _preview_keyboard()
    await thinking.edit_text(
        f"🎨 <b>Готово!</b>\n\n"
        f"<i>{summary}</i>\n\n"
        f"<b>Файлы ({len(site.files)}):</b>\n{files_preview}\n\n"
        f"📦 Размер: <b>{site.total_size_kb:.1f} KB</b>\n\n"
        "Куда задеплоить?",
        reply_markup=kb,
    )


@router.callback_query(F.data == CB_REGENERATE)
async def cb_regenerate(callback: CallbackQuery, state: FSMContext) -> None:
    """Regenerate with the same prompt.

    Note: we can't reuse `receive_prompt` directly because it expects a
    Message, not a CallbackQuery, and the typing of callback.message is
    InaccessibleMessage after edits. Instead we just clear state and
    ask the user to send the prompt again — simpler and less error-prone.
    """
    data = await state.get_data()
    prompt = data.get("prompt", "")
    if not prompt:
        await callback.answer("Нет сохранённого промта, начни заново: /site")
        await state.clear()
        return
    # Restore state to waiting_for_prompt and feed the prompt back
    await state.set_state(SiteFlow.waiting_for_prompt)
    await state.update_data(prompt=prompt)
    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"🔄 Регенерирую с тем же промтом:\n<i>{prompt[:200]}</i>\n\n" "🤖 Думаю..."
        )
    except Exception:  # noqa: BLE001
        pass

    # Invoke the prompt-receiver directly with a synthetic Message-like wrapper
    class _Msg:
        def __init__(self, text: str, from_user):
            self.text = text
            self.from_user = from_user

        async def answer(self, *args, **kwargs):
            return await callback.message.answer(*args, **kwargs)  # type: ignore[union-attr]

    synthetic = _Msg(prompt, callback.from_user)
    await receive_prompt(synthetic, state)  # type: ignore[arg-type]
    await callback.answer()


@router.callback_query(F.data == CB_CANCEL)
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Окей, отменил. /site — начать заново.")  # type: ignore[union-attr]
    await callback.answer()


@router.callback_query(F.data.in_({CB_DEPLOY_LAYERO, CB_DEPLOY_GITHUB}))
async def cb_deploy(callback: CallbackQuery, state: FSMContext) -> None:
    """Deploy to chosen target."""
    if callback.data == CB_DEPLOY_LAYERO:
        target = "Layero"
        action = "деплою на Layero"
        fn = layero.deploy_to_layero
        is_layero = True
    else:
        target = "GitHub"
        action = "пушу в GitHub"
        fn = github_export.export_to_github
        is_layero = False

    await callback.answer(f"{action}...")
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"⏳ {action.capitalize()}...\n<i>обычно 30-120 секунд</i>"
    )

    data = await state.get_data()
    site_dict = data.get("site")
    if not site_dict:
        await callback.message.edit_text(  # type: ignore[union-attr]
            "❌ Сайт не найден в FSM. Начни заново: /site"
        )
        await state.clear()
        return

    # Reconstruct GeneratedSite
    from bot.services.site_generator import GeneratedFile, GeneratedSite

    site = GeneratedSite(
        project_name=site_dict["project_name"],
        framework=site_dict["framework"],
        files=[GeneratedFile(**f) for f in site_dict["files"]],
        preview_summary=site_dict.get("preview_summary", ""),
    )

    try:
        result = await fn(site)
    except Exception as exc:  # noqa: BLE001
        logger.exception("deploy failed")
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"❌ Деплой на {target} упал: <code>{exc}</code>"
        )
        return

    if not result.success:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"❌ {target}: {result.message}"
        )
        return

    # Success
    if is_layero:
        url = result.url
        msg = f"✅ Задеплоено на Layero!\n\n🔗 <b>{url}</b>\n\nОткрой в браузере."
    else:
        url = result.repo_url
        msg = (
            f"✅ Запушено в GitHub!\n\n"
            f"🔗 <b>{url}</b>\n\n"
            f"Можешь клонировать или сразу подключить к Layero через GitHub App."
        )

    await callback.message.edit_text(  # type: ignore[union-attr]
        msg + "\n\n/site — создать ещё, /sites — мои сайты"
    )
    await state.clear()

    # Persist to Supabase (best-effort, non-blocking)
    if callback.from_user is not None:
        await _persist_site(site, callback.from_user.id, target, url)


async def _persist_site(
    site, tg_user_id: int, deploy_target: str, deploy_url: str
) -> None:
    """Save generated site to Supabase. Best-effort, never raises."""
    try:
        client = supabase.get_client()
        if client is None:
            logger.info("supabase unavailable, skipping persist")
            return
        client.table("sites").insert(
            {
                "tg_user_id": tg_user_id,
                "project_name": site.project_name,
                "framework": site.framework,
                "files_count": len(site.files),
                "size_kb": site.total_size_kb,
                "preview_summary": site.preview_summary,
                "deploy_target": deploy_target,
                "deploy_url": deploy_url,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("supabase persist failed: %s", exc)


def _preview_keyboard():
    """Inline keyboard for the preview screen."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Layero", callback_data=CB_DEPLOY_LAYERO),
                InlineKeyboardButton(text="📦 GitHub", callback_data=CB_DEPLOY_GITHUB),
            ],
            [
                InlineKeyboardButton(text="🔄 Переделать", callback_data=CB_REGENERATE),
                InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL),
            ],
        ]
    )
