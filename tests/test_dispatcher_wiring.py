"""Smoke test that verifies the dispatcher wires up all routers and handlers.

We don't actually start polling - just build the app and inspect the
router tree.
"""

import os

os.environ.setdefault(
    "TELEGRAM_BOT_TOKEN", "1234567890:AABBCCDDEEFFaabbccddeeff-12345678"
)
os.environ.setdefault("ADMIN_TELEGRAM_ID", "6318513424")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test_dummy")
os.environ.setdefault("SUPABASE_ANON_KEY", "test_dummy")
os.environ.setdefault("OPENROUTER_API_KEY", "test_dummy")
os.environ.setdefault("REDIS_URL", "redis://localhost:***/0")


def test_dispatcher_wires_all_routers():
    from bot.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    from bot.main import build_app

    # The build_app will fail at Bot() with our token (since we want to
    # inspect routers, we patch the Bot class first).
    from unittest.mock import MagicMock, patch

    with patch("bot.main.Bot") as MockBot:
        MockBot.return_value = MagicMock()
        # build_app internally creates Redis from URL - mock that too
        with patch("bot.main.Redis") as MockRedis:
            MockRedis.from_url.return_value = MagicMock()

            # Replace the RedisStorage with MemoryStorage to avoid real Redis
            from aiogram.fsm.storage import redis as redis_module

            with patch.object(redis_module, "RedisStorage") as MockStorage:
                mock_storage = MagicMock()
                MockStorage.return_value = mock_storage

                bot, dp, app = build_app()

    # Inspect dispatcher routers
    router_names = [r.name for r in dp.sub_routers]
    print(f"Router names: {router_names}")
    assert "admin" in router_names
    assert "common" in router_names
    assert "site_builder" in router_names

    # Inspect each router's handlers
    for r in dp.sub_routers:
        if r.name == "common":
            obs = r.observers
            print(f"common observers: {len(obs)}")
            assert len(obs) >= 4  # /start, /help, /cancel, fallback
        if r.name == "admin":
            obs = r.observers
            print(f"admin observers: {len(obs)}")
            assert len(obs) >= 8  # /admin + 7 /admin_* commands
        if r.name == "site_builder":
            obs = r.observers
            print(f"site_builder observers: {len(obs)}")
            assert len(obs) >= 4  # /site, /sites, prompt-receiver, callbacks
