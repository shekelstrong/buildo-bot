"""Buildo preview deployer — auto-deploys to Layero, returns preview URL.

Pipeline:
    GeneratedSite -> write files to ~/buildo-sites/<tg_id>/<site_id>/
                  -> npx layero deploy (or git push + auto-deploy webhook)
                  -> return preview URL

Strategy: we use the SAME layero account for all users (zero friction).
Each site gets a unique subpath: <site_id>.layero-app.buildo.ru
Phase 1.5: real custom domains.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.config import get_settings

logger = logging.getLogger(__name__)

# Site storage root (on server 108.165.164.85)
SITES_ROOT = Path.home() / "buildo-sites"

# Subprocess timeout for `npm install && npm run build` (60s should be enough)
BUILD_TIMEOUT = 90


@dataclass
class PreviewResult:
    """Result of a preview deploy attempt."""

    success: bool
    url: str = ""
    site_id: str = ""
    error: str = ""
    build_log: str = ""


def _safe_project_name(name: str) -> str:
    """kebab-case project name for filesystem + URL."""
    name = re.sub(r"[^a-z0-9-]+", "-", name.lower().strip())
    name = re.sub(r"-+", "-", name).strip("-")
    return name[:40] or "buildo-site"


def _site_dir(tg_user_id: int, site_id: str) -> Path:
    """Local directory for a single site version."""
    return SITES_ROOT / str(tg_user_id) / site_id


async def write_site_files(
    tg_user_id: int,
    site_id: str,
    files: list[dict[str, str]],
) -> Path:
    """Write all generated files to disk. Returns path to project dir.

    Files is a list of {path, content} dicts. Paths are relative to project root.
    """
    base = _site_dir(tg_user_id, site_id)
    base.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_event_loop()
    for f in files:
        rel = f["path"]
        # Security: prevent path traversal
        if ".." in rel.split("/"):
            raise ValueError(f"path traversal in file path: {rel}")
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        await loop.run_in_executor(None, target.write_text, f["content"], "utf-8")

    logger.info(
        "write_site_files ok tg=%d site=%s files=%d", tg_user_id, site_id, len(files)
    )
    return base


async def build_static_site(project_dir: Path) -> tuple[bool, str]:
    """Build a Vite+React project to dist/. Returns (success, log).

    We do `npm install --no-audit --prefer-offline` (faster) + `npm run build`.
    This is heavy; spawn in executor with timeout.
    """
    loop = asyncio.get_event_loop()

    def _build() -> tuple[int, str]:
        log_lines: list[str] = []
        env = os.environ.copy()
        env["CI"] = "1"  # suppress interactive prompts
        env["NODE_OPTIONS"] = "--max-old-space-size=2048"

        # 1) install
        proc = subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund", "--prefer-offline"],
            cwd=project_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT,
        )
        log_lines.append(f"[install exit={proc.returncode}]")
        log_lines.append(proc.stdout[-500:] if proc.stdout else "")
        if proc.returncode != 0:
            log_lines.append(proc.stderr[-1000:] if proc.stderr else "")
            return proc.returncode, "\n".join(log_lines)

        # 2) build
        proc = subprocess.run(
            ["npm", "run", "build"],
            cwd=project_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT,
        )
        log_lines.append(f"[build exit={proc.returncode}]")
        log_lines.append(proc.stdout[-1000:] if proc.stdout else "")
        if proc.returncode != 0:
            log_lines.append(proc.stderr[-2000:] if proc.stderr else "")
        return proc.returncode, "\n".join(log_lines)

    try:
        rc, log = await asyncio.wait_for(
            loop.run_in_executor(None, _build), timeout=BUILD_TIMEOUT + 30
        )
        return rc == 0, log
    except asyncio.TimeoutError:
        return False, "build timeout"
    except Exception as exc:  # noqa: BLE001
        return False, f"build exception: {exc}"


def _tar_dist(dist_dir: Path) -> bytes:
    """Tar.gz the dist/ directory. Returns bytes for upload."""
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        with tarfile.open(tmp.name, "w:gz") as tar:
            for p in dist_dir.rglob("*"):
                if p.is_file():
                    tar.add(p, arcname=p.relative_to(dist_dir))
        data = Path(tmp.name).read_bytes()
        Path(tmp.name).unlink()
        return data


def _write_dist_to_layero_dir(project_dir: Path, tg_user_id: int, site_id: str) -> Path:
    """For Phase 1 MVP without Layero API: write dist/ to a server dir
    that nginx serves as /sites/<site_id>/.

    Real Phase 1.5: replace with `npx layero deploy`.
    """
    target = SITES_ROOT / "public" / str(tg_user_id) / site_id
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(project_dir / "dist", target)
    return target


async def deploy_preview(
    tg_user_id: int,
    site_id: str,
    files: list[dict[str, str]],
    project_name: str,
) -> PreviewResult:
    """Full pipeline: write files -> npm install -> npm run build -> serve.

    Returns PreviewResult with URL to view the built site.
    """
    try:
        # 1) Write files
        project_dir = await write_site_files(tg_user_id, site_id, files)
        dist = project_dir / "dist"
        if not dist.exists():
            dist.mkdir(exist_ok=True)

        # 2) Build (skip if no package.json — static HTML only)
        if (project_dir / "package.json").exists():
            ok, log = await build_static_site(project_dir)
            if not ok:
                logger.error("build failed for site %s: %s", site_id, log[-500:])
                return PreviewResult(
                    success=False,
                    site_id=site_id,
                    error=f"build_failed: {log[-500:]}",
                    build_log=log,
                )

        # 3) Serve: copy to public dir for nginx
        _write_dist_to_layero_dir(project_dir, tg_user_id, site_id)

        # 4) URL: served by nginx at /sites/<tg_id>/<site_id>/
        # Phase 1.5: replace with real Layero URL
        settings = get_settings()
        url = f"https://buildo.ru/sites/{tg_user_id}/{site_id}/"
        # Fallback: just use host
        if not settings.webhook_url:
            url = f"http://108.165.164.85:9090/sites-static/{tg_user_id}/{site_id}/"

        logger.info(
            "deploy_preview ok tg=%d site=%s url=%s files=%d",
            tg_user_id,
            site_id,
            url,
            len(files),
        )
        return PreviewResult(success=True, url=url, site_id=site_id)

    except Exception as exc:  # noqa: BLE001
        logger.exception("deploy_preview failed")
        return PreviewResult(success=False, site_id=site_id, error=str(exc)[:500])


def list_versions(tg_user_id: int, site_id: str) -> list[dict[str, Any]]:
    """List all saved versions of a site (for Time Travel)."""
    site_path = _site_dir(tg_user_id, site_id)
    if not site_path.exists():
        return []
    versions = []
    for v in sorted(site_path.glob("v*"), reverse=True):
        meta = v / ".meta.json"
        if meta.exists():
            import json

            versions.append(json.loads(meta.read_text()))
    return versions


def get_version_files(
    tg_user_id: int, site_id: str, version: str
) -> list[dict[str, str]]:
    """Get all files for a specific version. Returns [{path, content}, ...]."""
    site_path = _site_dir(tg_user_id, site_id) / version
    if not site_path.exists():
        return []
    files = []
    for p in site_path.rglob("*"):
        if p.is_file() and not p.name.startswith("."):
            files.append(
                {
                    "path": str(p.relative_to(site_path)),
                    "content": p.read_text(encoding="utf-8"),
                }
            )
    return files


async def save_version(
    tg_user_id: int, site_id: str, version: str, files: list[dict[str, str]]
) -> None:
    """Save a snapshot of files (Time Travel). Version format: v1, v2, v3..."""
    base = _site_dir(tg_user_id, site_id) / version
    base.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_event_loop()
    for f in files:
        target = base / f["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        await loop.run_in_executor(None, target.write_text, f["content"], "utf-8")
    # Write meta
    import json
    from datetime import datetime, timezone

    meta = {
        "version": version,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "files_count": len(files),
    }
    (base / ".meta.json").write_text(json.dumps(meta), encoding="utf-8")


def next_version(tg_user_id: int, site_id: str) -> str:
    """Return next version label (v1, v2, ...) for a site."""
    site_path = _site_dir(tg_user_id, site_id)
    if not site_path.exists():
        return "v1"
    existing = [
        p.name for p in site_path.iterdir() if p.is_dir() and p.name.startswith("v")
    ]
    nums = []
    for v in existing:
        try:
            nums.append(int(v[1:]))
        except ValueError:
            pass
    return f"v{(max(nums) if nums else 0) + 1}"
