"""Tests for deploy services (Layero + GitHub export).

Most logic is integration with external CLIs. We mock subprocess to
test the wrapper logic.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.config import get_settings
from bot.services import github_export, layero
from bot.services.site_generator import GeneratedFile, GeneratedSite


def make_site() -> GeneratedSite:
    return GeneratedSite(
        project_name="test-coffee",
        framework="vite-react",
        files=[
            GeneratedFile(path="package.json", content='{"name":"test-coffee"}'),
            GeneratedFile(path="index.html", content="<html><body>Hi</body></html>"),
            GeneratedFile(
                path="src/App.jsx", content="export default function App(){}"
            ),
        ],
        preview_summary="A test coffee site.",
    )


@pytest.mark.asyncio
async def test_layero_no_token_returns_stub():
    s = get_settings()
    # token is dummy by default
    assert s.layero_api_token == "dummy_layero_token"

    site = make_site()
    res = await layero.deploy_to_layero(site)
    assert res.success is True
    assert "layero.ru" in res.url
    assert res.deploy_id == "stub-no-token"


@pytest.mark.asyncio
async def test_layero_no_cli_returns_stub():
    # Force a fresh settings with real-looking token
    with patch.object(layero, "_layero_cli_exists", return_value=False), patch.object(
        layero, "get_settings"
    ) as mock_gs:
        mock_settings = MagicMock()
        mock_settings.layero_api_token = "real-looking-token-but-no-cli"
        mock_gs.return_value = mock_settings

        site = make_site()
        res = await layero.deploy_to_layero(site)
        assert res.success is True
        assert res.deploy_id == "stub-no-cli"


@pytest.mark.asyncio
async def test_layero_parses_success_response():
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(
        return_value=(
            json.dumps(
                {"success": True, "url": "https://x.layero.ru", "deploy_id": "abc"}
            ).encode(),
            b"",
        )
    )
    with patch.object(layero, "_layero_cli_exists", return_value=True), patch.object(
        layero, "get_settings"
    ) as mock_gs, patch.object(
        layero.asyncio, "create_subprocess_exec", AsyncMock(return_value=mock_proc)
    ):
        mock_settings = MagicMock()
        mock_settings.layero_api_token = "real-looking-token"
        mock_gs.return_value = mock_settings

        site = make_site()
        res = await layero.deploy_to_layero(site)
        assert res.success is True
        assert res.url == "https://x.layero.ru"
        assert res.deploy_id == "abc"


@pytest.mark.asyncio
async def test_github_no_cli_returns_error():
    with patch.object(github_export, "_gh_cli_exists", return_value=False):
        site = make_site()
        res = await github_export.export_to_github(site)
        assert res.success is False
        assert "gh CLI" in res.message
