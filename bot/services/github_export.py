"""Buildo GitHub export — push generated sites to a shared GitHub org.

This is the "бекап + time travel + share-by-link" mechanism for Phase 1.5.

Architecture:
- All user sites live in one repo: shekelstrong/buildo-sites
- Each site is a subdir: sites/<tg_user_id>/<site_id>/
- Each deploy = commit, history = full git log
- URL: https://github.com/shekelstrong/buildo-sites/tree/main/sites/<tg_id>/<site_id>/

GitHub API used:
- PUT /repos/{owner}/{repo}/contents/{path} — create or update file
- GET /repos/{owner}/{owner}/{repo} — get default branch SHA
- POST /repos/{owner}/{repo}/pages — enable GitHub Pages (Phase 1.5)

Repo state: PRIVATE (per user requirement).
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from bot.config import get_settings

logger = logging.getLogger(__name__)

# Target repo: shekelstrong/buildo-sites (private)
GITHUB_REPO_OWNER = "shekelstrong"
GITHUB_REPO_NAME = "buildo-sites"
GITHUB_API_BASE = "https://api.github.com"


def _gh_headers() -> dict[str, str]:
    s = get_settings()
    token = s.github_token or ""
    return {
        "Authorization": f"Bearer {token}" if token else "",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Buildo-Bot/1.0",
    }


def _repo_configured() -> bool:
    s = get_settings()
    return bool(s.github_token)


async def _get_file_sha(
    client: httpx.AsyncClient, path: str, branch: str = "main"
) -> str | None:
    """Get SHA of existing file (None if doesn't exist)."""
    url = (
        f"{GITHUB_API_BASE}/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
        f"/contents/{path}?ref={branch}"
    )
    try:
        r = await client.get(url, headers=_gh_headers(), timeout=15)
        if r.status_code == 200:
            return r.json().get("sha")
        if r.status_code == 404:
            return None
        logger.warning(
            "github get_file_sha unexpected status %s: %s", r.status_code, r.text[:200]
        )
        return None
    except Exception:  # noqa: BLE001
        logger.exception("get_file_sha failed for %s", path)
        return None


async def push_files_to_repo(
    tg_user_id: int,
    site_id: str,
    files: list[dict[str, str]],
    commit_message: str,
) -> dict[str, Any]:
    """Push a generated site to GitHub repo.

    Args:
        tg_user_id: Telegram user ID (used in path for multi-tenancy)
        site_id: Unique site UUID
        files: List of {path, content} dicts (relative to site dir)
        commit_message: Git commit message

    Returns:
        {
            "success": bool,
            "commit_sha": str,
            "files_pushed": int,
            "repo_url": str,
            "error": str,
        }
    """
    if not _repo_configured():
        return {
            "success": False,
            "error": "GITHUB_TOKEN not configured in .env",
            "files_pushed": 0,
        }

    base_path = f"sites/{tg_user_id}/{site_id}"
    repo_url = f"https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
    pushed = 0
    last_sha = ""

    try:
        async with httpx.AsyncClient() as client:
            for f in files:
                rel_path = f["path"].lstrip("/")
                full_path = f"{base_path}/{rel_path}"
                # GitHub contents API limit: 100MB per file, 1000 files per commit
                content_b64 = base64.b64encode(f["content"].encode("utf-8")).decode(
                    "ascii"
                )
                existing_sha = await _get_file_sha(client, full_path)

                url = (
                    f"{GITHUB_API_BASE}/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
                    f"/contents/{full_path}"
                )
                payload: dict[str, Any] = {
                    "message": commit_message,
                    "content": content_b64,
                    "branch": "main",
                }
                if existing_sha:
                    payload["sha"] = existing_sha

                r = await client.put(
                    url, headers=_gh_headers(), json=payload, timeout=30
                )
                if r.status_code in (200, 201):
                    pushed += 1
                    data = r.json()
                    last_sha = data.get("commit", {}).get("sha", last_sha)
                else:
                    logger.error(
                        "github push failed %s: %s %s",
                        full_path,
                        r.status_code,
                        r.text[:300],
                    )
                    return {
                        "success": False,
                        "error": f"github push {rel_path}: {r.status_code} {r.text[:200]}",
                        "files_pushed": pushed,
                        "commit_sha": last_sha,
                        "repo_url": f"{repo_url}/tree/main/{base_path}",
                    }
        return {
            "success": True,
            "files_pushed": pushed,
            "commit_sha": last_sha,
            "repo_url": f"{repo_url}/tree/main/{base_path}",
            "site_zip_url": f"{repo_url}/raw/main/{base_path}/index.html",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("push_files_to_repo failed")
        return {
            "success": False,
            "error": str(exc)[:500],
            "files_pushed": pushed,
            "commit_sha": last_sha,
            "repo_url": f"{repo_url}/tree/main/{base_path}",
        }


async def create_github_pages_deploy(tg_user_id: int, site_id: str) -> dict[str, Any]:
    """Enable GitHub Pages for the user's site (Phase 1.5).

    Returns URL like https://shekelstrong.github.io/buildo-sites/sites/<tg_id>/<site_id>/
    """
    if not _repo_configured():
        return {"success": False, "error": "GITHUB_TOKEN not configured"}

    # GitHub Pages can be enabled at repo level only. For subpaths we use the
    # existing Pages deployment (already enabled at /).
    # The site is served from: /sites/<tg_id>/<site_id>/index.html
    base = f"sites/{tg_user_id}/{site_id}"
    pages_url = f"https://{GITHUB_REPO_OWNER}.github.io/{GITHUB_REPO_NAME}/{base}/"
    return {
        "success": True,
        "pages_url": pages_url,
        "note": "Подожди ~30 секунд пока GitHub опубликует (Actions → pages build).",
    }
