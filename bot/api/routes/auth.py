"""Auth API — for OAuth callbacks from buildo-web."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from bot.services import database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
async def get_me(tg_user_id: int = Query(6318513424)) -> dict[str, Any]:
    """Return current user info. MVP: hardcoded admin."""
    try:
        user = await database.upsert_tg_user(
            tg_user_id=tg_user_id,
            tg_username=None,
            tg_first_name="Web User",
            tg_last_name=None,
        )
        if not user or "id" not in user:
            raise HTTPException(status_code=500, detail="user_upsert_failed")
        return {
            "user": {
                "id": str(user["id"]),
                "name": user.get("tg_first_name") or "User",
                "email": f"tg:{user['tg_user_id']}@buildo.ru",
                "avatar": None,
                "plan": "free",
                "sites_limit": 1,
                "sites_used": 0,
                "created_at": str(user.get("created_at", "")),
            }
        }
    except Exception as e:
        logger.exception("get_me failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/oauth/callback")
async def oauth_callback(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle OAuth callback from NextAuth (web).

    MVP: stub. Phase 1.5: real OAuth state validation.
    """
    provider = payload.get("provider")
    tg_user_id = payload.get("tg_user_id", 6318513424)
    if not provider:
        raise HTTPException(status_code=400, detail="provider_required")

    user = await database.upsert_tg_user(
        tg_user_id=int(tg_user_id),
        tg_username=f"oauth_{provider}",
        tg_first_name=f"OAuth {provider}",
        tg_last_name=None,
    )
    return {"user": user, "provider": provider, "linked": True}
