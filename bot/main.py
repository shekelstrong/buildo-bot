"""Buildo bot entry point.

Run via: `python -m bot.main`
Or via Docker entrypoint.

Exposes (all on single FastAPI app, single port):
  - /health                  -- liveness probe
  - /api/v1/*                -- public API for buildo-web + buildo-miniapp
  - /sites-static/<tg>/<id>/ -- built site previews (static files)
  - Telegram bot (long polling, separate asyncio task)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from bot.api.app import create_api_app
from bot.config import get_settings
from bot.handlers import admin as admin_handlers
from bot.handlers import articles as articles_handlers
from bot.handlers import auth_github as auth_github_handlers
from bot.handlers import commands_escape as commands_escape_handlers
from bot.handlers import common as common_handlers
from bot.handlers import referral as referral_handlers
from bot.handlers import site_builder as site_handlers
from bot.middlewares import AdminFilter, LoggingMiddleware

logger = logging.getLogger(__name__)


def build_app() -> tuple[Bot, Dispatcher]:
    """Wire all bot components. Exposed for tests."""
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

    # CRITICAL: commands_escape router MUST be registered FIRST so it can
    # intercept /start, /cancel, "в меню" etc from any FSM state.
    dp.include_router(commands_escape_handlers.router)
    dp.include_router(admin_handlers.router)
    dp.include_router(site_handlers.router)
    dp.include_router(referral_handlers.router)
    dp.include_router(articles_handlers.router)
    dp.include_router(common_handlers.router)
    dp.include_router(auth_github_handlers.router)

    return bot, dp


def build_http_app() -> FastAPI:
    """Build single FastAPI app: health + public API + static sites on one port."""
    app = FastAPI(title="buildo-bot", version="0.1.0-mvp")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "buildo-bot"}

    # Mount public API under /api/v1
    api_app = create_api_app()
    app.mount("/api/v1", api_app)

    # Mount static site previews from ~/buildo-sites/public/
    # Each site is at /sites-static/<tg_id>/<site_id>/
    public_dir = Path.home() / "buildo-sites" / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/sites-static",
        StaticFiles(directory=str(public_dir), html=True),
        name="sites-static",
    )

    return app


async def run_polling() -> None:
    """Production entrypoint - long polling (default for tg bots)."""
    bot, dp = build_app()
    settings = get_settings()
    logger.info("Buildo bot starting (env=%s)", settings.environment)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


async def run_http() -> None:
    """Single FastAPI server: /health + /api/v1/* on 0.0.0.0:8080 (mapped to 9090 host)."""
    app = build_http_app()
    config = uvicorn.Config(
        app, host="0.0.0.0", port=get_settings().health_port, log_level="info"
    )
    server = uvicorn.Server(config)
    logger.info("HTTP server starting on port %d", get_settings().health_port)
    await server.serve()


async def main() -> None:
    """Combined: polling + HTTP (health + public API)."""
    await asyncio.gather(run_polling(), run_http())


if __name__ == "__main__":
    with suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(main())
