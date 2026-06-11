"""/site and /sites — site-builder flow (Phase 1 / MVP)."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

router = Router(name="site_builder")


class SiteFlow(StatesGroup):
    """FSM states for the site-building flow.

    States are minimal in the skeleton. Real implementation in Phase 1
    will add: waiting_for_prompt → waiting_for_template → preview → deploy.
    """

    waiting_for_prompt = State()
    waiting_for_template = State()
    preview = State()
    waiting_for_deploy_target = State()


@router.message(Command("site"))
async def cmd_site(message: Message, state: FSMContext) -> None:
    """Start the site-builder flow."""
    await state.set_state(SiteFlow.waiting_for_prompt)
    await message.answer(
        "🚀 <b>Создаём новый сайт</b>\n\n"
        "Опиши словами, что нужно сделать.\n"
        "Например:\n"
        "<i>«лендинг для кофейни в центре Москвы, тёплый минимализм»</i>\n"
        "<i>«портфолио веб-дизайнера с кейсами и контактами»</i>\n"
        "<i>«сайт-визитка для автосервиса»</i>\n\n"
        "Или нажми /cancel для отмены."
    )


@router.message(Command("sites"))
async def cmd_sites(message: Message) -> None:
    """List user's sites (stub in MVP, will fetch from Supabase in Phase 1)."""
    await message.answer(
        "📦 <b>Мои сайты</b>\n\n" "<i>Пока пусто. Создай первый через /site</i>"
    )


@router.message(SiteFlow.waiting_for_prompt)
async def receive_prompt(message: Message, state: FSMContext) -> None:
    """Receive user's prompt, show thinking state, then hand off to agent."""
    if message.text is None:
        await message.answer("Пришли текстом, что за сайт нужен.")
        return
    await state.update_data(prompt=message.text)
    await message.answer(
        f"✅ Принял: <i>{message.text[:200]}</i>\n\n"
        "🤖 Думаю над дизайном... (это заглушка, в Phase 1 будет реальный LLM-pipeline)"
    )
    await state.set_state(SiteFlow.waiting_for_template)


@router.message(SiteFlow.waiting_for_template)
async def choose_template(message: Message, state: FSMContext) -> None:
    """Template picker (stub)."""
    if message.text is None:
        return
    await state.set_state(SiteFlow.preview)
    await message.answer(
        "🎨 <b>Готово (демо)</b>\n\n"
        "Это заглушка превью. В Phase 1:\n"
        "1. Сгенерирую код через LLM\n"
        "2. Покажу превью и кнопки «задеплой» / «переделать»\n"
        "3. Дам выбрать Layero / Beget / GitHub\n"
    )
    await state.clear()
