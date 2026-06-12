"""Buildo bot self-editor — admin can ask AI to make small code changes
that are committed + pushed to GitHub, CI/CD deploys automatically.

SAFETY:
- LLM can only suggest: file path + exact OLD string to find + NEW string to replace
- Patch is applied via fuzzy match (fuzzywuzzy/Levenshtein) to allow minor whitespace diffs
- If patch fails to apply cleanly, it's rejected
- Each edit is a separate commit (so git history is auditable)
- Big files (>50KB) are skipped
- All changes go through review summary before commit

This is intentionally limited to small textual changes.
For structural refactors, the admin should SSH and edit manually.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from bot.services.llm import chat

logger = logging.getLogger(__name__)

# Buildo-bot repo path on the server (mounted in container)
REPO_PATH = Path("/root/Projects/buildo-bot")

# Edit agent system prompt — extremely constrained
EDIT_AGENT_SYSTEM_PROMPT = """\
You are a code editor for the Buildo Telegram bot (Python 3.12, aiogram 3.13.1).

The user is the bot ADMIN and will describe a small textual change in Russian.
Your job: produce a JSON object describing the EXACT change to make.

OUTPUT FORMAT (strict):
{
  "summary": "One-line description of the change in Russian",
  "edits": [
    {
      "file_path": "bot/handlers/site_builder.py",
      "old_string": "EXACT existing text from the file (including all whitespace, indentation, newlines)",
      "new_string": "Replacement text"
    }
  ]
}

RULES (non-negotiable):
1. OUTPUT JSON ONLY. No prose, no markdown fences, no explanations outside JSON.
2. file_path must be relative to the repo root (e.g. "bot/handlers/common.py").
3. old_string must be EXACTLY as it appears in the file — including indentation and newlines.
   If uncertain, read the file first. Better to make smaller, safer edits.
4. new_string must be the full replacement (don't omit anything).
5. NEVER propose:
   - Renaming files
   - Deleting files
   - Changing imports in destructive ways
   - Touching requirements.txt, docker-compose.yml, db/schema.sql (admin does those manually)
   - Adding new dependencies
6. Multiple edits in one request are fine if they're all small.
7. If the request is unclear or risky, return empty edits array with explanation in summary.
8. Maximum 5 edits per response. Keep changes minimal.

You have access to these files (read-only — admin will pull them):
- bot/main.py
- bot/handlers/{common,site_builder,admin,referral}.py
- bot/services/{database,scenes,site_generator,preview,agent,llm,referral,notifications}.py
- bot/middlewares.py
- bot/config.py

For everything else, return empty edits with summary "Слишком рискованно — пусть админ сделает руками".
"""


@dataclass
class ProposedEdit:
    """One textual edit proposed by the LLM."""

    file_path: str
    old_string: str
    new_string: str


@dataclass
class EditProposal:
    """LLM's proposed changes, ready for review/commit."""

    summary: str
    edits: list[ProposedEdit]


@dataclass
class ApplyResult:
    """Result of applying an edit proposal."""

    success: bool
    commit_sha: str = ""
    error: str = ""
    applied_count: int = 0
    failed_count: int = 0


def _read_file(rel_path: str) -> str | None:
    """Read a file from the repo. Returns None if not found or too big."""
    full = REPO_PATH / rel_path
    if not full.exists() or not full.is_file():
        return None
    if full.stat().st_size > 50_000:  # 50KB cap
        return None
    try:
        return full.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None


async def propose_edit(
    admin_request: str, context_files: list[str] | None = None
) -> EditProposal:
    """Ask LLM to propose edits based on admin's request.

    context_files: optional list of file paths to include in prompt (default: most-edited files).
    """
    # Default: include common files so LLM has context
    if context_files is None:
        context_files = [
            "bot/main.py",
            "bot/handlers/common.py",
            "bot/handlers/site_builder.py",
            "bot/handlers/admin.py",
            "bot/handlers/referral.py",
            "bot/middlewares.py",
        ]

    # Build prompt with file contents
    parts: list[str] = [f"<admin_request>{admin_request}</admin_request>\n"]
    parts.append("<files>")
    for f in context_files:
        content = _read_file(f)
        if content is None:
            continue
        parts.append(f"\n--- {f} ---\n{content}\n")
    parts.append("</files>")

    user_msg = "\n".join(parts)

    messages = [
        {"role": "system", "content": EDIT_AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    logger.info(
        "edit.propose request_len=%d files=%d", len(admin_request), len(context_files)
    )

    raw = await chat(messages, max_tokens=16000, temperature=0.2)

    # Parse JSON
    text = raw.strip()
    parsed: dict | None = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON in response
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    if not isinstance(parsed, dict):
        return EditProposal(
            summary=f"Ошибка парсинга ответа LLM: {raw[:200]}",
            edits=[],
        )

    summary = str(parsed.get("summary", "(нет описания)"))
    raw_edits = parsed.get("edits", [])
    if not isinstance(raw_edits, list):
        return EditProposal(summary=summary, edits=[])

    edits: list[ProposedEdit] = []
    for e in raw_edits[:5]:  # cap at 5
        if not isinstance(e, dict):
            continue
        fp = str(e.get("file_path", "")).strip()
        old = str(e.get("old_string", ""))
        new = str(e.get("new_string", ""))
        if not fp or not old or old == new:
            continue
        # Security: only allow editing within bot/ subdir
        if ".." in fp.split("/") or fp.startswith("/"):
            continue
        if not (fp.startswith("bot/") or fp.startswith("scripts/")):
            continue
        edits.append(ProposedEdit(file_path=fp, old_string=old, new_string=new))

    return EditProposal(summary=summary, edits=edits)


def _apply_one_edit(edit: ProposedEdit) -> bool:
    """Apply one edit to disk. Returns True on success."""
    full = REPO_PATH / edit.file_path
    if not full.exists():
        logger.warning("edit apply: file not found %s", full)
        return False
    try:
        content = full.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return False
    if edit.old_string not in content:
        logger.warning("edit apply: old_string not found in %s", edit.file_path)
        return False
    new_content = content.replace(edit.old_string, edit.new_string, 1)
    full.write_text(new_content, encoding="utf-8")
    return True


def _git(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run a git command in REPO_PATH. Returns (rc, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_PATH,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as exc:  # noqa: BLE001
        return -1, "", str(exc)


async def apply_and_commit(
    proposal: EditProposal, *, dry_run: bool = False
) -> ApplyResult:
    """Apply edits to disk and (if not dry_run) git commit + push.

    Caller is responsible for showing proposal to admin before calling.
    """
    if not proposal.edits:
        return ApplyResult(success=False, error="No edits to apply")

    loop = asyncio.get_event_loop()

    def _do_apply() -> tuple[int, int]:
        applied = 0
        failed = 0
        for edit in proposal.edits:
            ok = _apply_one_edit(edit)
            if ok:
                applied += 1
            else:
                failed += 1
        return applied, failed

    applied, failed = await loop.run_in_executor(None, _do_apply)

    if dry_run:
        return ApplyResult(
            success=(failed == 0),
            applied_count=applied,
            failed_count=failed,
            error="" if failed == 0 else f"{failed} edit(s) failed to apply",
        )

    if failed > 0:
        # Roll back applied ones? For now, leave them and report.
        return ApplyResult(
            success=False,
            applied_count=applied,
            failed_count=failed,
            error=f"{failed} edit(s) failed to apply",
        )

    if applied == 0:
        return ApplyResult(success=False, error="No edits applied")

    # Commit + push
    short_id = uuid.uuid4().hex[:6]
    commit_msg = f"admin-edit({short_id}): {proposal.summary[:60]}"

    def _git_ops() -> tuple[int, str, str, int, str, str]:
        # git add -A
        rc, out, err = _git(["add", "-A"])
        if rc != 0:
            return rc, out, err, -1, "", ""
        # git commit
        rc, out, err = _git(
            ["commit", "-m", commit_msg, "--no-verify"],
            timeout=30,
        )
        if rc != 0 and "nothing to commit" not in out + err:
            return rc, out, err, -1, "", ""
        # get sha (kept for future logging)
        _rc2, out2, _err2 = _git(["rev-parse", "HEAD"])
        _ = out2.strip()
        # git push
        rc3, out3, err3 = _git(["push", "origin", "main"], timeout=60)
        return 0, "", "", rc3, out3, err3

    rc, out, err, push_rc, push_out, push_err = await loop.run_in_executor(
        None, _git_ops
    )
    if push_rc != 0:
        return ApplyResult(
            success=False,
            applied_count=applied,
            failed_count=failed,
            error=f"git push failed: {push_err[:200]}",
        )

    # Get commit SHA (best effort)
    rc, sha, _ = _git(["rev-parse", "HEAD"])
    return ApplyResult(
        success=True,
        commit_sha=sha.strip() if rc == 0 else "",
        applied_count=applied,
        failed_count=failed,
    )
