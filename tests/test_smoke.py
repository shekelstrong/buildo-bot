"""Smoke tests for buildo-bot.

Verify bot imports and key services wire up. Real integration tests
land in Phase 1 once Supabase is connected.
"""

import os


# Force-set (overwrite) env vars so this test is hermetic regardless
# of what the caller has in their environment.
os.environ["TELEGRAM_BOT_TOKEN"] = "smoketest_dummy_token"
os.environ["ADMIN_TELEGRAM_ID"] = "6318513424"
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_SERVICE_KEY"] = "test_dummy"
os.environ["SUPABASE_ANON_KEY"] = "test_dummy"
os.environ["OPENROUTER_API_KEY"] = "test_dummy"
os.environ["REDIS_URL"] = "redis://localhost:***/0"


def test_settings_loads():
    from bot.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    s = get_settings()
    assert s.admin_telegram_id == 6318513424
    assert s.telegram_bot_token == "smoketest_dummy_token"


def test_admin_filter():
    from bot.middlewares import AdminFilter

    f = AdminFilter(admin_id=6318513424)
    assert f is not None


def test_services_import():
    from bot.services import admin, database, llm, supabase

    assert admin.get_stats is not None
    assert llm.chat is not None
    assert database.get_pool is not None
    assert supabase.upsert_tg_user is not None  # re-exported shim


def test_admin_stats_returns_dict():
    import asyncio

    from bot.services.admin import get_stats

    s = asyncio.run(get_stats())
    # Required keys for /admin_stats
    assert "users_total" in s
    assert "sites_deployed" in s
