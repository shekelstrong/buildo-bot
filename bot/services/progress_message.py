"""ProgressMessage — одно Telegram-сообщение которое обновляется на каждом этапе.

Использование:
    progress = ProgressMessage(bot, chat_id)
    await progress.start("🔍 Анализирую...")
    await progress.update("🎨 Подбираю стиль...")
    await progress.finish("✦ Готово! ...")

Behaviour:
- start() — отправляет send_message с `parse_mode="HTML"`.
- update() — пытается edit_message_text (HTML). Если Telegram отвергает
  HTML (например, "Unsupported start tag" — бывает когда enriched prompt
  содержит LLM-разметку <section>), пробует edit БЕЗ parse_mode. Если и
  это падает, отправляет новое сообщение через send_message.
- finish() / fail() — то же, что update() но с остановкой typing.
- Любые ошибки логируются и **никогда не теряются** — пользователь
  гарантированно видит финал.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)


class ProgressMessage:
    """Одно сообщение которое обновляется через edit_text."""

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self._message: Optional[Message] = None
        self._current_text: str = ""
        self._typing_task: Optional[asyncio.Task] = None
        self._stop_typing = asyncio.Event()

    async def attach_to_last_bot_message(self) -> bool:
        """Bind to a previously-sent bot message by ID.

        This is a no-op when no message_id is set; the caller must
        pass it via `attach(message_id)`. Kept for API symmetry.
        """
        return self._message is not None

    async def attach(self, message: Message) -> None:
        """Bind to an already-sent message (no new message created).

        Subsequent update()/finish()/fail() will edit THIS message
        instead of sending a new one.
        """
        self._message = message
        self._current_text = message.text or message.caption or ""
        # No typing loop needed — message already exists
        self._stop_typing.set()

    async def start(
        self, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> None:
        """Отправить начальное сообщение и запустить индикатор 'печатает...'."""
        self._current_text = text
        msg = await self._send_safe(text, reply_markup=reply_markup)
        if msg is not None:
            self._message = msg
            self._start_typing_loop()
            return
        # Если даже start() упал — пробуем plain text без HTML
        try:
            self._message = await self.bot.send_message(
                chat_id=self.chat_id, text=text, reply_markup=reply_markup
            )
            self._start_typing_loop()
        except TelegramAPIError as exc:
            logger.warning("ProgressMessage.start plain-text fallback failed: %s", exc)

    async def update(
        self, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> None:
        """Обновить текст того же сообщения.

        Стратегия fallback (по убыванию):
        1. edit_message_text с parse_mode="HTML"
        2. edit_message_text БЕЗ parse_mode (plain)
        3. send_message БЕЗ parse_mode (новое сообщение)
        """
        if self._message is None:
            # Если start() не сработал — отправляем новое сообщение
            await self._send_new_safe(text, reply_markup=reply_markup)
            return

        self._current_text = text

        # 1) HTML edit
        try:
            await self.bot.edit_message_text(
                text=text,
                chat_id=self.chat_id,
                message_id=self._message.message_id,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            return
        except TelegramRetryAfter as exc:
            logger.warning("TelegramRetryAfter in update: %s, waiting", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            await self.update(text, reply_markup)
            return
        except TelegramAPIError as exc:
            err_text = str(exc).lower()
            if "not modified" in err_text:
                # OK — текст уже такой же
                return
            logger.warning("ProgressMessage.update HTML failed: %s", exc)

        # 2) Plain edit (no parse_mode)
        try:
            await self.bot.edit_message_text(
                text=text,
                chat_id=self.chat_id,
                message_id=self._message.message_id,
                reply_markup=reply_markup,
            )
            return
        except TelegramAPIError as exc:
            err_text = str(exc).lower()
            if "not modified" in err_text:
                return
            logger.warning("ProgressMessage.update plain edit failed: %s", exc)

        # 3) New message (последний шанс — юзер должен УВИДЕТЬ текст)
        await self._send_new_safe(text, reply_markup=reply_markup)

    async def finish(
        self, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> None:
        """Финальное сообщение. Останавливает 'печатает...'."""
        await self._stop_typing_loop()
        await self.update(text, reply_markup=reply_markup)

    async def fail(self, text: str) -> None:
        """Сообщение об ошибке."""
        await self._stop_typing_loop()
        await self.update(f"⚠️ {text}")

    async def _send_safe(
        self, text: str, reply_markup: InlineKeyboardMarkup | None
    ) -> Message | None:
        """send_message с HTML, fallback на plain text. Возвращает Message или None."""
        try:
            return await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except TelegramAPIError as exc:
            logger.warning("send_message HTML failed: %s — trying plain", exc)
        try:
            return await self.bot.send_message(
                chat_id=self.chat_id, text=text, reply_markup=reply_markup
            )
        except TelegramAPIError as exc:
            logger.warning("send_message plain failed: %s", exc)
            return None

    async def _send_new_safe(
        self, text: str, reply_markup: InlineKeyboardMarkup | None
    ) -> None:
        """send_message как новое сообщение (когда edit не сработал)."""
        msg = await self._send_safe(text, reply_markup=reply_markup)
        if msg is not None:
            self._message = msg  # теперь это наше текущее сообщение

    def _start_typing_loop(self) -> None:
        """Запустить фоновую задачу которая каждые 4с шлёт 'typing'."""
        self._stop_typing.clear()

        async def loop() -> None:
            try:
                while not self._stop_typing.is_set():
                    try:
                        await self.bot.send_chat_action(
                            chat_id=self.chat_id,
                            action="typing",
                        )
                    except TelegramAPIError:
                        pass
                    try:
                        # Ждём 4с или до отмены
                        await asyncio.wait_for(self._stop_typing.wait(), timeout=4.0)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                pass

        self._typing_task = asyncio.create_task(loop())

    async def _stop_typing_loop(self) -> None:
        """Остановить фоновую задачу typing."""
        if self._typing_task is None:
            return
        self._stop_typing.set()
        try:
            await asyncio.wait_for(self._typing_task, timeout=1.0)
        except asyncio.TimeoutError:
            self._typing_task.cancel()
        except Exception:  # noqa: BLE001
            pass
        self._typing_task = None
