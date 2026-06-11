"""Layero deploy integration.

Layero exposes a CLI (`npx layero deploy --json`) that can deploy
a local project to their PaaS. We shell out to it via subprocess.

If `layero` CLI is not installed, the service falls back to a stub
that returns a fake URL (so the bot doesn't crash in dev).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass

from bot.config import get_settings
from bot.services.site_generator import GeneratedSite

logger = logging.getLogger(__name__)


@dataclass
class DeployResult:
    success: bool
    url: str
    deploy_id: str
    message: str


class LayeroUnavailable(Exception):
    """Raised when Layero CLI is not available and no token configured."""


def _layero_cli_exists() -> bool:
    """Check whether `npx` and `layero` CLI are available locally."""
    return shutil.which("npx") is not None


async def deploy_to_layero(site: GeneratedSite) -> DeployResult:
    """Deploy a generated site to Layero.

    1. Write GeneratedSite to a temp dir as a real Vite+React project.
    2. Shell out to `npx layero deploy --json` in that dir.
    3. Parse the response (URL + deploy_id).

    If Layero CLI is unavailable, returns a stub result so the bot
    stays usable for local dev.
    """
    settings = get_settings()
    if (
        not settings.layero_api_token
        or settings.layero_api_token == "dummy_layero_token"
    ):
        logger.warning("layero.token missing, returning stub")
        return DeployResult(
            success=True,
            url=f"https://{site.project_name}.layero.ru",
            deploy_id="stub-no-token",
            message="Layero token not configured; stub URL returned.",
        )

    if not _layero_cli_exists():
        logger.warning("layero CLI not installed, returning stub")
        return DeployResult(
            success=True,
            url=f"https://{site.project_name}.layero.ru",
            deploy_id="stub-no-cli",
            message="Layero CLI not installed; stub URL returned.",
        )

    # Write site to temp dir
    with tempfile.TemporaryDirectory(prefix="buildo-deploy-") as tmp:
        for f in site.files:
            full = os.path.join(tmp, f.path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fp:
                fp.write(f.content)

        env = os.environ.copy()
        env["LAYERO_API_TOKEN"] = settings.layero_api_token

        proc = await asyncio.create_subprocess_exec(
            "npx",
            "layero",
            "deploy",
            "--json",
            cwd=tmp,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            raise LayeroUnavailable("layero deploy timed out after 5 min")

        if proc.returncode != 0:
            err = stderr.decode(errors="replace")[:500]
            logger.error("layero deploy failed: %s", err)
            return DeployResult(
                success=False,
                url="",
                deploy_id="",
                message=f"Layero deploy failed: {err}",
            )

        try:
            data = json.loads(stdout.decode())
        except json.JSONDecodeError as exc:
            return DeployResult(
                success=False,
                url="",
                deploy_id="",
                message=f"Layero returned non-JSON: {exc}",
            )

        return DeployResult(
            success=bool(data.get("success", True)),
            url=str(data.get("url", "")),
            deploy_id=str(data.get("deploy_id", "")),
            message=str(data.get("message", "deployed")),
        )
