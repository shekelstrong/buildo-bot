"""Logging middleware — records every incoming event with structured log."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        user_id = getattr(user, "id", None)
        username = getattr(user, "username", None)

        if isinstance(event, Message):
            logger.info(
                "msg from %s (%s): %r",
                user_id,
                username,
                (event.text or event.caption or "<media>")[:200],
            )
        elif isinstance(event, CallbackQuery):
            logger.info("callback from %s (%s): %s", user_id, username, event.data)
        return await handler(event, data)
