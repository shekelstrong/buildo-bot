"""Sites API endpoints — for buildo-web dashboard and buildo-miniapp."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from bot.services import database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sites", tags=["sites"])


@router.get("")
async def list_sites(
    tg_user_id: int = Query(
        6318513424, description="Telegram user ID (MVP: hardcoded admin)"
    ),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """List all sites for a Telegram user."""
    try:
        sites = await database.list_user_sites(tg_user_id, limit=limit)
        return {"sites": sites, "total": len(sites)}
    except Exception as e:
        logger.exception("list_sites failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_site(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a new site from a prompt.

    MVP: just creates a project entry. Phase 1.5: triggers LLM generation.
    """
    prompt = (payload.get("prompt") or "").strip()
    tg_user_id = int(payload.get("tg_user_id", 6318513424))

    if len(prompt) < 10:
        raise HTTPException(
            status_code=400,
            detail={"error": "prompt_too_short", "min_length": 10},
        )

    project_name = " ".join(prompt.split()[:3]) or "Untitled"

    try:
        # Ensure user exists
        user = await database.upsert_tg_user(
            tg_user_id=tg_user_id,
            tg_username=None,
            tg_first_name="Web User",
            tg_last_name=None,
        )
        if not user or "id" not in user:
            raise HTTPException(status_code=500, detail="user_upsert_failed")

        site = await database.save_site(
            user_id=user["id"],
            project_name=project_name,
            framework="vite-react",
            files_count=0,
            size_kb=0.0,
            preview_summary=prompt[:200],
            deploy_target="layero",
            deploy_url="",
            prompt=prompt,
        )
        if not site or "error" in (site or {}):
            err = (site or {}).get("error", "save_failed")
            raise HTTPException(
                status_code=403 if err == "free_tier_limit" else 500,
                detail=err,
            )
        return {"site": site}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("create_site failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{site_id}")
async def get_site(site_id: str) -> dict[str, Any]:
    """Get details of a single site by ID."""
    try:
        site = await database.get_site(site_id)
        if not site:
            raise HTTPException(status_code=404, detail="site_not_found")
        return {"site": site}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_site failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{site_id}")
async def delete_site(site_id: str) -> dict[str, Any]:
    """Soft-delete a site (mark as deleted via redeploy_site proxy for MVP)."""
    try:
        ok = await database.redeploy_site(site_id)
        return {"ok": bool(ok)}
    except Exception as e:
        logger.exception("delete_site failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
