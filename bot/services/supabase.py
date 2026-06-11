"""Backward-compat shim - delegates to bot.services.database.

Kept so existing imports of `from bot.services import supabase` work.
All actual logic is in bot.services.database (psycopg-based, async).
"""
from typing import Any

# Re-export async functions for backward compat
from bot.services.database import (  # type: ignore[no-redef]
    upsert_tg_user as upsert_tg_user,  # noqa: F401
    get_user_by_tg as get_user_by_tg,  # noqa: F401
    ban_user as ban_user,  # noqa: F401
    save_site as save_site,  # noqa: F401
    list_user_sites as list_user_sites,  # noqa: F401
    redeploy_site as redeploy_site,  # noqa: F401
    get_platform_stats as get_platform_stats,  # noqa: F401
    get_recent_users as get_recent_users,  # noqa: F401
    get_recent_payments as get_recent_payments,  # noqa: F401
    log_action as log_action,  # noqa: F401
)


async def get_client() -> Any:
    """Legacy stub. Returns None - DB connection is now per-call via pool."""
    return None


def reset_client() -> None:
    """Legacy stub. No-op in the new async pool world."""
    return None
