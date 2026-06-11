"""Supabase service - real integration with the Buildo project.

In MVP, queries are best-effort. If client is None (no config or
unavailable), we silently skip. Phase 1.5 will add error surfacing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)
_client: "Client | None" = None


def get_client() -> "Client | None":
    """Lazy-init Supabase client. Returns None if no real key/URL."""
    global _client
    if _client is not None:
        return _client
    try:
        from supabase import create_client

        from bot.config import get_settings

        s = get_settings()
        if (
            not s.supabase_url
            or not s.supabase_service_key
            or s.supabase_url == "https://your-project.supabase.co"
            or s.supabase_service_key == "dummy_service_key"
        ):
            logger.info("supabase using placeholder config - running in stub mode")
            return None
        _client = create_client(s.supabase_url, s.supabase_service_key)
        return _client
    except Exception as exc:  # noqa: BLE001
        logger.warning("supabase init failed: %s", exc)
        return None


def reset_client() -> None:
    """Reset the cached client (for tests)."""
    global _client
    _client = None


# =====================================================================
# USER OPERATIONS
# =====================================================================


def upsert_tg_user(
    tg_user_id: int,
    tg_username: str | None = None,
    tg_first_name: str | None = None,
    tg_last_name: str | None = None,
) -> dict[str, Any] | None:
    """Create or update a Telegram user. Returns the row, or None if Supabase unavailable."""
    client = get_client()
    if client is None:
        return None
    try:
        data = {
            "tg_user_id": tg_user_id,
            "tg_username": tg_username,
            "tg_first_name": tg_first_name,
            "tg_last_name": tg_last_name,
        }
        # Use upsert via Postgres ON CONFLICT
        result = client.table("users").upsert(data, on_conflict="tg_user_id").execute()
        return result.data[0] if result.data else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("upsert_tg_user failed: %s", exc)
        return None


def get_user_by_tg(tg_user_id: int) -> dict[str, Any] | None:
    """Fetch a user by Telegram ID."""
    client = get_client()
    if client is None:
        return None
    try:
        result = (
            client.table("users").select("*").eq("tg_user_id", tg_user_id).execute()
        )
        return result.data[0] if result.data else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_user_by_tg failed: %s", exc)
        return None


def ban_user(tg_user_id: int) -> bool:
    """Ban a user. Returns True on success."""
    client = get_client()
    if client is None:
        return False
    try:
        client.table("users").update({"is_banned": True}).eq(
            "tg_user_id", tg_user_id
        ).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("ban_user failed: %s", exc)
        return False


# =====================================================================
# SITE OPERATIONS
# =====================================================================


def save_site(
    user_id: int,
    project_name: str,
    framework: str,
    files_count: int,
    size_kb: float,
    preview_summary: str,
    deploy_target: str,
    deploy_url: str,
    prompt: str,
) -> dict[str, Any] | None:
    """Save a generated + deployed site. Returns the site row."""
    client = get_client()
    if client is None:
        return None
    try:
        # 1) Create project
        proj = (
            client.table("projects")
            .insert(
                {
                    "user_id": user_id,
                    "project_name": project_name,
                    "framework": framework,
                    "prompt": prompt,
                    "files_count": files_count,
                    "size_kb": size_kb,
                    "preview_summary": preview_summary,
                }
            )
            .execute()
        )
        project_id = proj.data[0]["id"] if proj.data else None

        # 2) Create site
        site = (
            client.table("sites")
            .insert(
                {
                    "user_id": user_id,
                    "project_id": project_id,
                    "project_name": project_name,
                    "deploy_target": deploy_target,
                    "deploy_url": deploy_url,
                    "status": "deployed",
                    "last_deploy_at": datetime.utcnow().isoformat(),
                }
            )
            .execute()
        )
        return site.data[0] if site.data else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("save_site failed: %s", exc)
        return None


def list_user_sites(tg_user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """List sites for a Telegram user."""
    client = get_client()
    if client is None:
        return []
    try:
        # Simple query: get user_id from tg_user_id, then list sites
        u = client.table("users").select("id").eq("tg_user_id", tg_user_id).execute()
        if not u.data:
            return []
        uid = u.data[0]["id"]
        sites = (
            client.table("sites")
            .select("*")
            .eq("user_id", uid)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return list(sites.data or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_user_sites failed: %s", exc)
        return []


def redeploy_site(site_id: str) -> bool:
    """Mark a site for re-deploy (updates last_deploy_at)."""
    client = get_client()
    if client is None:
        return False
    try:
        client.table("sites").update(
            {"last_deploy_at": datetime.utcnow().isoformat(), "status": "building"}
        ).eq("id", site_id).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("redeploy_site failed: %s", exc)
        return False


# =====================================================================
# ADMIN / PLATFORM STATS
# =====================================================================


def get_platform_stats() -> dict[str, int] | None:
    """Read v_platform_stats view. Returns None if Supabase unavailable."""
    client = get_client()
    if client is None:
        return None
    try:
        result = client.table("v_platform_stats").select("*").limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_platform_stats failed: %s", exc)
        return None


def get_recent_users(limit: int = 50) -> list[dict[str, Any]]:
    """Read v_recent_users view."""
    client = get_client()
    if client is None:
        return []
    try:
        # View doesn't support LIMIT directly; we limit via query param
        result = (
            client.table("v_recent_users").select("*").range(0, limit - 1).execute()
        )
        return list(result.data or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_recent_users failed: %s", exc)
        return []


def get_recent_payments(limit: int = 50) -> list[dict[str, Any]]:
    """Read v_recent_payments view."""
    client = get_client()
    if client is None:
        return []
    try:
        result = (
            client.table("v_recent_payments").select("*").range(0, limit - 1).execute()
        )
        return list(result.data or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_recent_payments failed: %s", exc)
        return []


# =====================================================================
# AUDIT
# =====================================================================


def log_action(
    actor_id: int, action: str, target: str | None = None, **metadata
) -> None:
    """Write to audit_log. Best-effort, never raises."""
    client = get_client()
    if client is None:
        return
    try:
        client.table("audit_log").insert(
            {
                "actor_id": actor_id,
                "action": action,
                "target": target,
                "metadata": metadata or {},
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("log_action failed: %s", exc)
