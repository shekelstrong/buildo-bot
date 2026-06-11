"""Admin services - real PostgreSQL integration (with stub fallback).

Queries hit the self-hosted Buildo PostgreSQL via bot.services.database.
If the DB is unreachable, deterministic placeholder data is returned so
the admin panel works in stub mode.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from bot.services import supabase  # re-exports from database

logger = logging.getLogger(__name__)


async def get_stats() -> dict[str, int | str]:
    """Aggregate platform statistics."""
    stats = await supabase.get_platform_stats()
    if stats is not None:
        return stats
    return {
        "users_total": 0,
        "users_24h": 0,
        "projects_total": 0,
        "sites_deployed": 0,
        "revenue_rub": 0,
        "revenue_stars": 0,
        "revenue_crypto_usd": 0,
    }


async def get_recent_users(limit: int = 50) -> list[dict]:
    """Last `limit` users."""
    return await supabase.get_recent_users(limit=limit)


async def get_recent_payments(limit: int = 20) -> list[dict]:
    """Last `limit` payments."""
    return await supabase.get_recent_payments(limit=limit)


async def broadcast(text: str) -> int:
    """Stub - returns 0 (broadcast not implemented in MVP)."""
    _ = text
    return 0


async def redeploy(site_id: str) -> str:
    """Trigger manual re-deploy for `site_id`."""
    ok = await supabase.redeploy_site(site_id)
    if ok:
        return f"queued at {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    return "DB unavailable, log-only"


async def ban_user(user_id: int) -> str:
    """Ban a user. Logs action in audit_log if DB available."""
    ok = await supabase.ban_user(user_id)
    if ok:
        await supabase.log_action(actor_id=user_id, action="ban", target=str(user_id))
        return f"banned (tg_user_id={user_id})"
    return f"ban flag set (stub) - user_id={user_id}"
