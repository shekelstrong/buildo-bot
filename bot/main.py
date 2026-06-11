"""Buildo bot entry point.

Run via: `python -m bot.main`
Or via Docker entrypoint.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from fastapi import FastAPI
from redis.asyncio import Redis

from bot.config import get_settings
from bot.handlers import admin as admin_handlers
from bot.handlers import common as common_handlers
from bot.handlers import site_builder as site_handlers
from bot.middlewares import AdminFilter, LoggingMiddleware

logger = logging.getLogger(__name__)


def build_app() -> tuple[Bot, Dispatcher, FastAPI]:
    """Wire all components. Exposed for tests."""
    settings = get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    storage = RedisStorage(redis=redis)

    dp = Dispatcher(storage=storage)
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())

    admin_router_filter = AdminFilter(admin_id=settings.admin_telegram_id)
    admin_handlers.router.message.filter(admin_router_filter)
    admin_handlers.router.callback_query.filter(admin_router_filter)

    dp.include_router(admin_handlers.router)
    dp.include_router(site_handlers.router)
    dp.include_router(common_handlers.router)

    return bot, dp, None


async def run_polling() -> None:
    """Production entrypoint - long polling (default for tg bots)."""
    bot, dp, _ = build_app()
    settings = get_settings()
    logger.info("Buildo bot starting (env=%s)", settings.environment)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


async def run_health() -> None:
    """Run FastAPI health server in parallel with polling.

    Creates a separate FastAPI app to avoid double-include_router on the Dispatcher.
    """
    app = FastAPI(title="buildo-bot", version="0.1.0-mvp")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "buildo-bot"}

    config = uvicorn.Config(
        app, host="0.0.0.0", port=get_settings().health_port, log_level="warning"
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    """Combined: polling + health endpoint."""
    await asyncio.gather(run_polling(), run_health())


if __name__ == "__main__":
    with suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(main())
