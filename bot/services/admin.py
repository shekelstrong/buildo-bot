"""Admin services — stub layer, will be backed by Supabase in Phase 1.

All functions return deterministic placeholder data so the admin panel
works end-to-end even before the database is wired up. Replace each
function body with real Supabase queries in Phase 1.
"""

from __future__ import annotations

from datetime import datetime, timezone


async def get_stats() -> dict[str, int | str]:
    """Aggregate platform statistics. Stub returns 0s."""
    return {
        "users_total": 0,
        "users_24h": 0,
        "sites_total": 0,
        "sites_deployed": 0,
        "revenue_rub": 0,
        "revenue_stars": 0,
        "revenue_crypto_usd": 0,
    }


async def get_recent_users(limit: int = 50) -> list[dict]:
    """Last `limit` users. Stub returns empty list."""
    return []


async def get_recent_payments(limit: int = 20) -> list[dict]:
    """Last `limit` payments. Stub returns empty list."""
    return []


async def broadcast(text: str) -> int:
    """Send `text` to all users. Stub returns 0 (no users yet)."""
    _ = text  # placeholder until we have a real user list
    return 0


async def redeploy(site_id: str) -> str:
    """Trigger manual re-deploy for `site_id`. Stub always succeeds."""
    return f"queued at {datetime.now(timezone.utc).isoformat(timespec='seconds')}"


async def ban_user(user_id: int) -> str:
    """Ban a user. Stub logs only."""
    return f"ban flag set (stub) — user_id={user_id}"
