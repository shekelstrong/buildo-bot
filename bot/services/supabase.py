"""Supabase service — minimal client wrapper.

In MVP, this is a thin client around `supabase-py`. Real queries land
in Phase 1. Single shared client per process (lazy init).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)
_client: "Client | None" = None


def get_client() -> "Client | None":
    """Lazy-init Supabase client. Returns None in MVP if no real key."""
    global _client
    if _client is not None:
        return _client
    try:
        from supabase import create_client

        from bot.config import get_settings

        s = get_settings()
        if s.supabase_url.startswith("https://your-project"):
            logger.warning("supabase using placeholder URL — running in stub mode")
            return None
        _client = create_client(s.supabase_url, s.supabase_service_key)
        return _client
    except Exception as exc:  # noqa: BLE001
        logger.warning("supabase init failed: %s", exc)
        return None
