"""ProgressMessage — одно Telegram-сообщение которое обновляется на каждом этапе.

Использование:
    progress = ProgressMessage(bot, chat_id)
    await progress.start("🔍 Анализирую...")
    await progress.update("🎨 Подбираю стиль...")
    await progress.finish("✦ Готово! ...")
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

    async def start(
        self, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> None:
        """Отправить начальное сообщение и запустить индикатор 'печатает...'."""
        self._current_text = text
        try:
            self._message = await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=reply_markup,
            )
        except (TelegramAPIError, TelegramRetryAfter) as exc:
            logger.warning("ProgressMessage.start failed: %s", exc)
            return
        self._start_typing_loop()

    async def update(
        self, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> None:
        """Обновить текст того же сообщения. Кнопки можно передать для финала."""
        if self._message is None:
            return
        self._current_text = text
        try:
            await self.bot.edit_message_text(
                text=text,
                chat_id=self.chat_id,
                message_id=self._message.message_id,
                reply_markup=reply_markup,
            )
        except TelegramRetryAfter as exc:
            # Telegram требует подождать N секунд
            logger.warning("TelegramRetryAfter: %s, waiting", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            await self.update(text, reply_markup)
        except TelegramAPIError as exc:
            # "message is not modified" — игнорируем
            if "not modified" not in str(exc).lower():
                logger.warning("ProgressMessage.update failed: %s", exc)

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
