"""Tests for GitHub Device Flow + auth_github handler.

Все тесты мокают HTTP через unittest.mock, чтобы не зависеть от respx.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

os.environ.setdefault(
    "TELEGRAM_BOT_TOKEN", "1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)
os.environ.setdefault("ADMIN_TELEGRAM_ID", "6318513424")
os.environ.setdefault("ENCRYPTION_KEY", "")


# =====================================================================
# SERVICE: github_device_flow
# =====================================================================


def test_device_flow_module_imports():
    from bot.services import github_device_flow

    assert hasattr(github_device_flow, "start_device_flow")
    assert hasattr(github_device_flow, "poll_for_token")
    assert hasattr(github_device_flow, "validate_token_and_get_username")


def test_device_flow_urls():
    from bot.services.github_device_flow import (
        GITHUB_ACCESS_TOKEN_URL,
        GITHUB_DEVICE_CODE_URL,
        GITHUB_USER_URL,
    )

    assert GITHUB_DEVICE_CODE_URL == "https://github.com/login/device/code"
    assert GITHUB_ACCESS_TOKEN_URL == "https://github.com/login/oauth/access_token"
    assert GITHUB_USER_URL == "https://api.github.com/user"


def test_device_flow_settings_have_oauth_client_id():
    from bot.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    s = get_settings()
    assert s.github_oauth_client_id, "github_oauth_client_id must be set"
    assert s.github_oauth_scopes, "github_oauth_scopes must be set"
    assert "repo" in s.github_oauth_scopes


def test_headers_use_json_accept():
    from bot.services.github_device_flow import _device_flow_headers

    h = _device_flow_headers()
    assert h["Accept"] == "application/json"
    assert "User-Agent" in h


def test_start_device_flow_parses_response():
    import asyncio

    from bot.services.github_device_flow import start_device_flow

    async def run() -> None:
        fake_response = AsyncMock()
        fake_response.status_code = 200
        fake_response.json = lambda: {
            "device_code": "abc123",
            "user_code": "WDHB-MHTP",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
        }
        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.post = AsyncMock(return_value=fake_response)

        with patch("httpx.AsyncClient", return_value=fake_client):
            result = await start_device_flow()
            assert result["device_code"] == "abc123"
            assert result["user_code"] == "WDHB-MHTP"
            assert result["interval"] == 5
            assert result["expires_in"] == 900

    asyncio.run(run())


def test_start_device_flow_handles_error():
    import asyncio

    from bot.services.github_device_flow import start_device_flow

    async def run() -> None:
        fake_response = AsyncMock()
        fake_response.status_code = 403
        fake_response.text = '{"error": "bad client_id"}'
        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.post = AsyncMock(return_value=fake_response)

        with patch("httpx.AsyncClient", return_value=fake_client):
            try:
                await start_device_flow()
                assert False, "expected RuntimeError"
            except RuntimeError as e:
                assert "403" in str(e)

    asyncio.run(run())


def test_start_device_flow_validates_required_fields():
    """Если GitHub вернул неполный JSON — RuntimeError."""
    import asyncio

    from bot.services.github_device_flow import start_device_flow

    async def run() -> None:
        fake_response = AsyncMock()
        fake_response.status_code = 200
        fake_response.json = lambda: {"device_code": "x"}  # no user_code etc
        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.post = AsyncMock(return_value=fake_response)

        with patch("httpx.AsyncClient", return_value=fake_client):
            try:
                await start_device_flow()
                assert False, "expected RuntimeError"
            except RuntimeError as e:
                assert "неполный" in str(e) or "missing" in str(e).lower()

    asyncio.run(run())


def test_validate_token_returns_username():
    import asyncio

    from bot.services.github_device_flow import validate_token_and_get_username

    async def run() -> None:
        fake_response = AsyncMock()
        fake_response.status_code = 200
        fake_response.json = lambda: {"login": "testuser", "id": 123}
        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.get = AsyncMock(return_value=fake_response)

        with patch("httpx.AsyncClient", return_value=fake_client):
            username = await validate_token_and_get_username("fake_token_xyz")
            assert username == "testuser"

    asyncio.run(run())


def test_validate_token_invalid_returns_error():
    import asyncio

    from bot.services.github_device_flow import validate_token_and_get_username

    async def run() -> None:
        fake_response = AsyncMock()
        fake_response.status_code = 401
        fake_response.text = '{"message": "Bad credentials"}'
        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.get = AsyncMock(return_value=fake_response)

        with patch("httpx.AsyncClient", return_value=fake_client):
            try:
                await validate_token_and_get_username("bad")
                assert False, "expected RuntimeError"
            except RuntimeError as e:
                assert "401" in str(e) or "валиден" in str(e)

    asyncio.run(run())


# =====================================================================
# HANDLER: auth_github — FSM states
# =====================================================================


def test_auth_github_fsm_states():
    from bot.handlers.auth_github import GitHubAuthFSM

    states = {s.state for s in GitHubAuthFSM.__states__}
    assert "GitHubAuthFSM:waiting_for_pat" in states
    assert "GitHubAuthFSM:waiting_for_device_confirm" in states


def test_auth_github_router_registered():
    """Роутер должен быть зарегистрирован в main.py и иметь observers."""
    from bot.handlers.auth_github import router as gh_router
    import bot.main as bot_main

    # Проверяем что import есть
    assert hasattr(
        bot_main, "auth_github_handlers"
    ), "main.py должен импортировать auth_github_handlers"

    # Роутер имеет имя и observers
    assert gh_router.name == "github-auth"
    assert len(gh_router.observers) > 0, "router has no observers"
    # Минимум: cmd_github, cb_github_connect, cb_github_disconnect, receive_pat
    assert (
        len(gh_router.observers) >= 4
    ), f"expected ≥4 observers (cmd, callbacks, state), got {len(gh_router.observers)}"


# =====================================================================
# DATABASE: new functions for OAuth
# =====================================================================


def test_database_has_github_oauth_functions():
    from bot.services import database

    for fn in [
        "set_user_github_oauth",
        "clear_user_github",
        "get_user_github_info",
        "get_user_github_token",
    ]:
        assert hasattr(database, fn), f"missing {fn}"


# =====================================================================
# INTEGRATION: encrypt/decrypt round-trip
# =====================================================================


def test_encrypt_decrypt_roundtrip():
    """Encrypt + decrypt должны давать исходный токен."""
    from cryptography.fernet import Fernet

    from bot.services.github_export import decrypt_token, encrypt_token

    # Устанавливаем ключ Fernet
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")
    from bot.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    original = "ghp_FAKE_TOKEN_FOR_TEST_12345"
    encrypted = encrypt_token(original)
    assert encrypted != original
    assert len(encrypted) > 0
    decrypted = decrypt_token(encrypted)
    assert decrypted == original
