"""Admin services - real Supabase integration (with stub fallback).

If SUPABASE_URL and SUPABASE_SERVICE_KEY are configured in .env,
queries hit the real DB. Otherwise, deterministic placeholder data is
returned so the admin panel works in dev/stub mode.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from bot.services import supabase

logger = logging.getLogger(__name__)


async def get_stats() -> dict[str, int | str]:
    """Aggregate platform statistics. Uses Supabase view if available."""
    stats = supabase.get_platform_stats()
    if stats is not None:
        return stats
    # Stub fallback
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
    """Last `limit` users. Uses Supabase view if available."""
    return supabase.get_recent_users(limit=limit)


async def get_recent_payments(limit: int = 20) -> list[dict]:
    """Last `limit` payments. Uses Supabase view if available."""
    return supabase.get_recent_payments(limit=limit)


async def broadcast(text: str) -> int:
    """Send `text` to all users. Stub returns 0 (no users yet)."""
    _ = text
    return 0


async def redeploy(site_id: str) -> str:
    """Trigger manual re-deploy for `site_id`."""
    ok = supabase.redeploy_site(site_id)
    if ok:
        return f"queued at {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    return "supabase unavailable, log-only"


async def ban_user(user_id: int) -> str:
    """Ban a user. Logs action in audit_log if Supabase available."""
    ok = supabase.ban_user(user_id)
    if ok:
        supabase.log_action(actor_id=user_id, action="ban", target=str(user_id))
        return f"banned (tg_user_id={user_id})"
    return f"ban flag set (stub) — user_id={user_id}"
