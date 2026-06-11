"""PostgreSQL database service using async psycopg.

This replaces the supabase client for the self-hosted Buildo deployment.
Same API surface as the previous supabase service so handlers don't need
to change.

Connection: postgres://buildo:***@buildo-postgres:5432/buildo (Docker internal)
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from bot.config import get_settings

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None


async def get_pool() -> AsyncConnectionPool | None:
    """Lazy-init async connection pool. Returns None if no DB configured."""
    global _pool
    if _pool is not None:
        return _pool

    s = get_settings()
    if not s.postgres_dsn or "your" in s.postgres_dsn or "dummy" in s.postgres_dsn:
        logger.info("postgres DSN not configured, running in stub mode")
        return None

    try:
        _pool = AsyncConnectionPool(
            conninfo=s.postgres_dsn,
            min_size=2,
            max_size=10,
            timeout=10,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        await _pool.open()
        await _pool.wait()
        logger.info("postgres pool opened")
        return _pool
    except Exception as exc:  # noqa: BLE001
        logger.warning("postgres pool init failed: %s", exc)
        _pool = None
        return None


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# =====================================================================
# USER OPERATIONS
# =====================================================================


async def upsert_tg_user(
    tg_user_id: int,
    tg_username: str | None = None,
    tg_first_name: str | None = None,
    tg_last_name: str | None = None,
) -> dict[str, Any] | None:
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO users (tg_user_id, tg_username, tg_first_name, tg_last_name)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (tg_user_id) DO UPDATE
                      SET tg_username = COALESCE(EXCLUDED.tg_username, users.tg_username),
                          tg_first_name = COALESCE(EXCLUDED.tg_first_name, users.tg_first_name),
                          tg_last_name = COALESCE(EXCLUDED.tg_last_name, users.tg_last_name),
                          updated_at = now()
                    RETURNING *
                    """,
                    (tg_user_id, tg_username, tg_first_name, tg_last_name),
                )
                return await cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("upsert_tg_user failed: %s", exc)
        return None


async def get_user_by_tg(tg_user_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM users WHERE tg_user_id = %s", (tg_user_id,)
                )
                return await cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_user_by_tg failed: %s", exc)
        return None


async def ban_user(tg_user_id: int) -> bool:
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE users SET is_banned = TRUE, updated_at = now() WHERE tg_user_id = %s",
                    (tg_user_id,),
                )
                return cur.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("ban_user failed: %s", exc)
        return False


# =====================================================================
# SITE OPERATIONS
# =====================================================================


async def save_site(
    user_id: int,
    project_name: str,
    framework: str,
    files_count: int,
    size_kb: float,
    preview_summary: str,
    deploy_target: str,
    deploy_url: str,
    prompt: str,
    site_id: str | None = None,
) -> dict[str, Any] | None:
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # 1) project
                await cur.execute(
                    """
                    INSERT INTO projects (user_id, project_name, framework, prompt,
                                          files_count, size_kb, preview_summary)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        user_id,
                        project_name,
                        framework,
                        prompt,
                        files_count,
                        size_kb,
                        preview_summary,
                    ),
                )
                row = await cur.fetchone()
                project_id = row["id"] if row else None

                # 2) site
                if site_id:
                    await cur.execute(
                        """
                        INSERT INTO sites (id, user_id, project_id, project_name,
                                           deploy_target, deploy_url, status, last_deploy_at)
                        VALUES (%s, %s, %s, %s, %s, %s, 'deployed', now())
                        RETURNING *
                        """,
                        (
                            site_id,
                            user_id,
                            project_id,
                            project_name,
                            deploy_target,
                            deploy_url,
                        ),
                    )
                else:
                    await cur.execute(
                        """
                        INSERT INTO sites (user_id, project_id, project_name,
                                           deploy_target, deploy_url, status, last_deploy_at)
                        VALUES (%s, %s, %s, %s, %s, 'deployed', now())
                        RETURNING *
                        """,
                        (user_id, project_id, project_name, deploy_target, deploy_url),
                    )
                return await cur.fetchone()
    except psycopg.errors.RaiseException as exc:
        # Free tier limit hit
        logger.info("save_site free-tier block: %s", exc)
        return {"error": "free_tier_limit", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("save_site failed: %s", exc)
        return None


async def update_site_deploy(site_id: str, deploy_url: str) -> bool:
    """Update deploy URL for a site after a fresh deploy."""
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE sites SET deploy_url = %s, last_deploy_at = now() WHERE id = %s",
                    (deploy_url, site_id),
                )
                return cur.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_site_deploy failed: %s", exc)
        return False


async def update_site_status(site_id: str, status: str, deploy_url: str = "") -> bool:
    """Update site status (draft, published, deleted)."""
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                if deploy_url:
                    await cur.execute(
                        "UPDATE sites SET status = %s, deploy_url = %s WHERE id = %s",
                        (status, deploy_url, site_id),
                    )
                else:
                    await cur.execute(
                        "UPDATE sites SET status = %s WHERE id = %s",
                        (status, site_id),
                    )
                return cur.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_site_status failed: %s", exc)
        return False


async def list_user_sites(tg_user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    pool = await get_pool()
    if pool is None:
        return []
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT s.*
                    FROM sites s
                    JOIN users u ON u.id = s.user_id
                    WHERE u.tg_user_id = %s
                    ORDER BY s.created_at DESC
                    LIMIT %s
                    """,
                    (tg_user_id, limit),
                )
                return list(await cur.fetchall())
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_user_sites failed: %s", exc)
        return []


async def delete_site(site_id: str) -> bool:
    """Hard-delete a site row. Use with care (cascades to versions)."""
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM sites WHERE id = %s", (site_id,))
                return cur.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_site failed: %s", exc)
        return False


async def redeploy_site(site_id: str) -> bool:
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE sites
                    SET last_deploy_at = now(), status = 'building'
                    WHERE id = %s
                    """,
                    (site_id,),
                )
                return cur.rowcount > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("redeploy_site failed: %s", exc)
        return False


# =====================================================================
# ADMIN / PLATFORM STATS
# =====================================================================


async def get_platform_stats() -> dict[str, int] | None:
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT * FROM v_platform_stats")
                return await cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_platform_stats failed: %s", exc)
        return None


async def get_recent_users(limit: int = 50) -> list[dict[str, Any]]:
    pool = await get_pool()
    if pool is None:
        return []
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT * FROM v_recent_users LIMIT %s", (limit,))
                return list(await cur.fetchall())
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_recent_users failed: %s", exc)
        return []


async def get_recent_payments(limit: int = 50) -> list[dict[str, Any]]:
    pool = await get_pool()
    if pool is None:
        return []
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT * FROM v_recent_payments LIMIT %s", (limit,))
                return list(await cur.fetchall())
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_recent_payments failed: %s", exc)
        return []


# =====================================================================
# AUDIT
# =====================================================================


async def log_action(
    actor_id: int, action: str, target: str | None = None, **metadata
) -> None:
    pool = await get_pool()
    if pool is None:
        return
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO audit_log (actor_id, action, target, metadata)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (actor_id, action, target, json.dumps(metadata)),
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("log_action failed: %s", exc)
