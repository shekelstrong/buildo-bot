"""Buildo GitHub export — push generated sites to GitHub.

Architecture:
- User's own GitHub (PAT token, encrypted in DB) — primary path
- Fallback: shekelstrong/buildo-sites (shared private repo)

For user's GitHub: bot creates a new repo `<username>/buildo-sites` or pushes
to existing one. Token is Fernet-encrypted at rest in users table.

GitHub API:
- PUT /repos/{owner}/{repo}/contents/{path} — create or update file
- POST /user/repos — create new repo (under user's account)
- GET /repos/{owner}/{repo} — check if exists

Repo state: PRIVATE (per user requirement).
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from bot.config import get_settings

logger = logging.getLogger(__name__)

# Shared fallback repo (shekelstrong)
FALLBACK_REPO_OWNER = "shekelstrong"
FALLBACK_REPO_NAME = "buildo-sites"

GITHUB_API_BASE = "https://api.github.com"


def _fernet():
    """Получить Fernet-инстанс для шифрования токенов."""
    from cryptography.fernet import Fernet

    s = get_settings()
    key = s.encryption_key
    if not key:
        raise ValueError("ENCRYPTION_KEY not set in env")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plain: str) -> str:
    """Зашифровать GitHub токен для хранения в БД."""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_token(encrypted: str) -> str:
    """Расшифровать GitHub токен из БД."""
    return _fernet().decrypt(encrypted.encode("ascii")).decode("utf-8")


def _gh_headers(token: str) -> dict[str, str]:
    """Заголовки для GitHub API с переданным токеном."""
    return {
        "Authorization": f"Bearer {token}" if token else "",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Buildo-Bot/1.0",
    }


def _bot_token_headers() -> dict[str, str]:
    """Заголовки с токеном бота (fallback)."""
    s = get_settings()
    token = s.github_token or ""
    return _gh_headers(token)


def _bot_repo_configured() -> bool:
    s = get_settings()
    return bool(s.github_token)


async def validate_user_token(token: str) -> dict[str, Any]:
    """Проверить валидность GitHub PAT и получить username.

    Args:
        token: GitHub Personal Access Token (raw, unencrypted)

    Returns:
        {
            "valid": bool,
            "username": str | None,
            "error": str | None,
        }
    """
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{GITHUB_API_BASE}/user",
                headers=_gh_headers(token),
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "valid": True,
                    "username": data.get("login"),
                    "error": None,
                }
            return {
                "valid": False,
                "username": None,
                "error": f"GitHub вернул {r.status_code}: {r.text[:200]}",
            }
    except Exception as exc:  # noqa: BLE001
        return {"valid": False, "username": None, "error": str(exc)[:200]}


async def _get_file_sha(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    path: str,
    token: str,
    branch: str = "main",
) -> str | None:
    """Get SHA of existing file (None if doesn't exist)."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    try:
        r = await client.get(url, headers=_gh_headers(token), timeout=15)
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


async def _ensure_user_repo(
    client: httpx.AsyncClient,
    username: str,
    repo_name: str,
    token: str,
) -> bool:
    """Создать репо <username>/<repo_name> если не существует. Private."""
    check_url = f"{GITHUB_API_BASE}/repos/{username}/{repo_name}"
    r = await client.get(check_url, headers=_gh_headers(token), timeout=10)
    if r.status_code == 200:
        return True
    if r.status_code == 404:
        # Создаём
        create_url = f"{GITHUB_API_BASE}/user/repos"
        payload = {
            "name": repo_name,
            "description": "My sites built with Buildo Bot",
            "private": True,
            "auto_init": True,
        }
        cr = await client.post(
            create_url, headers=_gh_headers(token), json=payload, timeout=15
        )
        if cr.status_code in (200, 201):
            logger.info("Created repo %s/%s", username, repo_name)
            return True
        logger.error(
            "Failed to create repo %s/%s: %s %s",
            username,
            repo_name,
            cr.status_code,
            cr.text[:300],
        )
        return False
    return False


async def push_files_to_user_repo(
    github_token: str,
    github_username: str,
    tg_user_id: int,
    site_id: str,
    files: list[dict[str, str]],
    commit_message: str,
) -> dict[str, Any]:
    """Push a generated site to user's own GitHub.

    Creates <username>/buildo-sites if doesn't exist (private).
    """
    repo_name = "buildo-sites"
    base_path = f"sites/{tg_user_id}/{site_id}"
    repo_url = f"https://github.com/{github_username}/{repo_name}"
    pushed = 0
    last_sha = ""

    try:
        async with httpx.AsyncClient() as client:
            ok = await _ensure_user_repo(
                client, github_username, repo_name, github_token
            )
            if not ok:
                return {
                    "success": False,
                    "error": f"не удалось создать/найти репо {github_username}/{repo_name}",
                    "files_pushed": 0,
                    "repo_url": repo_url,
                }

            for f in files:
                rel_path = f["path"].lstrip("/")
                full_path = f"{base_path}/{rel_path}"
                content_b64 = base64.b64encode(f["content"].encode("utf-8")).decode(
                    "ascii"
                )
                existing_sha = await _get_file_sha(
                    client, github_username, repo_name, full_path, github_token
                )

                url = (
                    f"{GITHUB_API_BASE}/repos/{github_username}/{repo_name}"
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
                    url, headers=_gh_headers(github_token), json=payload, timeout=30
                )
                if r.status_code in (200, 201):
                    pushed += 1
                    data = r.json()
                    last_sha = data.get("commit", {}).get("sha", last_sha)
                else:
                    logger.error(
                        "user-repo push failed %s: %s %s",
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
        logger.exception("push_files_to_user_repo failed")
        return {
            "success": False,
            "error": str(exc)[:500],
            "files_pushed": pushed,
            "commit_sha": last_sha,
            "repo_url": f"{repo_url}/tree/main/{base_path}",
        }


async def push_files_to_repo(
    tg_user_id: int,
    site_id: str,
    files: list[dict[str, str]],
    commit_message: str,
) -> dict[str, Any]:
    """Fallback: push to shekelstrong/buildo-sites (shared private repo)."""
    if not _bot_repo_configured():
        return {
            "success": False,
            "error": "GITHUB_TOKEN not configured in .env",
            "files_pushed": 0,
        }

    base_path = f"sites/{tg_user_id}/{site_id}"
    repo_url = f"https://github.com/{FALLBACK_REPO_OWNER}/{FALLBACK_REPO_NAME}"
    pushed = 0
    last_sha = ""

    try:
        async with httpx.AsyncClient() as client:
            for f in files:
                rel_path = f["path"].lstrip("/")
                full_path = f"{base_path}/{rel_path}"
                content_b64 = base64.b64encode(f["content"].encode("utf-8")).decode(
                    "ascii"
                )
                existing_sha = await _get_file_sha(
                    client,
                    FALLBACK_REPO_OWNER,
                    FALLBACK_REPO_NAME,
                    full_path,
                    get_settings().github_token or "",
                )

                url = (
                    f"{GITHUB_API_BASE}/repos/{FALLBACK_REPO_OWNER}/{FALLBACK_REPO_NAME}"
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
                    url, headers=_bot_token_headers(), json=payload, timeout=30
                )
                if r.status_code in (200, 201):
                    pushed += 1
                    data = r.json()
                    last_sha = data.get("commit", {}).get("sha", last_sha)
                else:
                    logger.error(
                        "fallback-repo push failed %s: %s %s",
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
        logger.exception("push_files_to_repo (fallback) failed")
        return {
            "success": False,
            "error": str(exc)[:500],
            "files_pushed": pushed,
            "commit_sha": last_sha,
            "repo_url": f"{repo_url}/tree/main/{base_path}",
        }


async def create_github_pages_deploy(
    tg_user_id: int,
    site_id: str,
    github_username: str | None = None,
) -> dict[str, Any]:
    """GitHub Pages URL.

    Returns URL like:
    - https://<username>.github.io/buildo-sites/sites/<tg_id>/<site_id>/
    - или fallback: https://shekelstrong.github.io/buildo-sites/...
    """
    if github_username:
        base = f"sites/{tg_user_id}/{site_id}"
        return {
            "success": True,
            "pages_url": (f"https://{github_username}.github.io/buildo-sites/{base}/"),
            "note": (
                "Если впервые — включи Pages в Settings → Pages → Source: GitHub Actions. "
                "Подожди ~30 секунд пока GitHub опубликует."
            ),
        }

    if not _bot_repo_configured():
        return {"success": False, "error": "GITHUB_TOKEN not configured"}

    base = f"sites/{tg_user_id}/{site_id}"
    return {
        "success": True,
        "pages_url": (
            f"https://{FALLBACK_REPO_OWNER}.github.io/{FALLBACK_REPO_NAME}/{base}/"
        ),
        "note": "Подожди ~30 секунд пока GitHub опубликует.",
    }
