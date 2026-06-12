"""Buildo GitHub Device Flow — OAuth без callback URL.

Why Device Flow (а не Web OAuth):
  - TMA живёт в t.me, не имеет https callback
  - Device Flow не требует client secret (public client)
  - User code UX: 1 browser hop, ~15 секунд
  - Дока: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow

Flow:
  1. start_device_flow() → POST /login/device/code
     → возвращает {device_code, user_code, verification_uri, interval, expires_in}
  2. Бот background-task поллит POST /login/oauth/access_token каждые interval сек
     - authorization_pending → продолжаем
     - slow_down → interval + 5
     - access_token → encrypt + store + отмена polling
     - expired_token / access_denied → ошибка
  3. validate_token() → GET /user → username

In-memory state: храним device_code в FSM context (`state.github_device_code`).
In-DB state: после success сохраняем encrypted token в users.github_token_encrypted.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from bot.config import get_settings

logger = logging.getLogger(__name__)

GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


def _device_flow_headers() -> dict[str, str]:
    """Заголовки для Device Flow — Accept JSON критичен (по умолчанию GitHub
    отдаёт urlencoded, что ломает парсинг)."""
    return {
        "Accept": "application/json",
        "User-Agent": "Buildo-Bot/1.0",
    }


async def start_device_flow() -> dict[str, Any]:
    """Шаг 1: запросить device_code + user_code.

    Returns:
        {
            "device_code": "...",
            "user_code": "WDHB-MHTP",
            "verification_uri": "https://github.com/login/device",
            "interval": 5,
            "expires_in": 900,
        }

    Raises:
        RuntimeError on GitHub API error
    """
    s = get_settings()
    payload = {
        "client_id": s.github_oauth_client_id,
        "scope": s.github_oauth_scopes,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                GITHUB_DEVICE_CODE_URL,
                data=payload,
                headers=_device_flow_headers(),
            )
            if r.status_code != 200:
                logger.error(
                    "github device flow start failed: %s %s",
                    r.status_code,
                    r.text[:300],
                )
                raise RuntimeError(
                    f"GitHub вернул {r.status_code} при старте Device Flow"
                )
            data = r.json()
            required = {"device_code", "user_code", "verification_uri", "expires_in"}
            missing = required - data.keys()
            if missing:
                raise RuntimeError(
                    f"GitHub Device Flow ответ неполный: нет полей {missing}"
                )
            data.setdefault("interval", 5)
            return data
    except httpx.HTTPError as exc:
        logger.exception("device flow network error")
        raise RuntimeError(f"Сеть: {exc}") from exc


async def poll_for_token(
    device_code: str,
    interval: int = 5,
    expires_in: int = 900,
) -> dict[str, Any]:
    """Шаг 2: поллинг access_token (background-task).

    Блокирует до успеха, отказа или таймаута. Polling respects `slow_down`
    (увеличиваем interval на 5).

    Returns:
        {"access_token": "...", "token_type": "bearer", "scope": "repo"}

    Raises:
        asyncio.TimeoutError если expires_in истёк
        PermissionError если user нажал Deny
        RuntimeError для прочих ошибок GitHub
    """
    s = get_settings()
    payload = {
        "client_id": s.github_oauth_client_id,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    current_interval = interval
    deadline = expires_in  # seconds

    async with httpx.AsyncClient(timeout=15) as client:
        for _ in range(deadline // current_interval + 1):
            await asyncio.sleep(current_interval)
            try:
                r = await client.post(
                    GITHUB_ACCESS_TOKEN_URL,
                    data=payload,
                    headers=_device_flow_headers(),
                )
                if r.status_code != 200:
                    logger.warning("device flow poll status %s", r.status_code)
                    continue
                data = r.json()
                if "access_token" in data:
                    return data
                err = data.get("error", "")
                if err == "authorization_pending":
                    continue
                if err == "slow_down":
                    current_interval += 5
                    logger.info("device flow slow_down, interval=%s", current_interval)
                    continue
                if err == "expired_token":
                    raise RuntimeError("Время вышло. Запусти /github connect заново.")
                if err == "access_denied":
                    raise PermissionError(
                        "Юзер отменил авторизацию на GitHub. Попробуй ещё раз."
                    )
                if err == "incorrect_device_code":
                    raise RuntimeError("device_code невалиден (bug)")
                # Unknown error
                logger.error("device flow unknown error: %s", data)
                raise RuntimeError(f"GitHub: {err or data}")
            except httpx.HTTPError as exc:
                logger.warning("device flow poll network: %s", exc)
                continue
    raise asyncio.TimeoutError("Device Flow истёк (15 мин)")


async def validate_token_and_get_username(token: str) -> str:
    """Шаг 3: проверить access_token через GET /user → получить username."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Buildo-Bot/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(GITHUB_USER_URL, headers=headers)
            if r.status_code != 200:
                raise RuntimeError(f"Token невалиден: GitHub вернул {r.status_code}")
            return r.json().get("login", "")
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Сеть: {exc}") from exc
