"""Buildo preview deployer — auto-deploys PURE STATIC sites, returns preview URL.

Pipeline:
    GeneratedSite (static HTML, no build) -> write files to ~/buildo-sites/<tg_id>/<site_id>/
                                         -> served by FastAPI as /sites-static/<tg_id>/<site_id>/

Why no Layero deploy in MVP:
- Phase 1 MVP: just serve static files from our own server via FastAPI StaticFiles
- Phase 1.5: add Layero auto-deploy when LAYERO_API_TOKEN is wired up
- Zero build step: pure HTML works on any static host, no Node.js dependency
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Site storage root (on server 108.165.164.85)
SITES_ROOT = Path.home() / "buildo-sites"


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
    For static-html framework, the single index.html is placed at root.
    """
    base = _site_dir(tg_user_id, site_id)
    # Wipe old version (atomic — this is "preview", not "production")
    if base.exists():
        loop = asyncio.get_event_loop()

        def _rmtree() -> None:
            shutil.rmtree(base, ignore_errors=True)

        await loop.run_in_executor(None, _rmtree)
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


def _write_dist_to_public(project_dir: Path, tg_user_id: int, site_id: str) -> Path:
    """For Phase 1 MVP: serve directly from project_dir via FastAPI StaticFiles.

    The api/main.py mounts ~/buildo-sites/public as /sites-static/.
    For static-html framework, the project_dir IS the public dir.
    """
    target = SITES_ROOT / "public" / str(tg_user_id) / site_id
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(project_dir, target)
    return target


async def deploy_preview(
    tg_user_id: int,
    site_id: str,
    files: list[dict[str, str]],
    project_name: str,
) -> PreviewResult:
    """Full pipeline: write files -> copy to public dir -> return URL.

    No build step (pure static). Returns PreviewResult with URL to view site.
    """
    try:
        # 1) Write files to project dir
        project_dir = await write_site_files(tg_user_id, site_id, files)

        # 2) Copy to public dir (served by FastAPI StaticFiles)
        _write_dist_to_public(project_dir, tg_user_id, site_id)

        # 3) URL
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
