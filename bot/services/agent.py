"""Buildo Agent — dialog-based site editing.

When user is in 'editing' mode, they write natural language ("поменяй hero на тёмный"),
and this agent:
  1. Reads current files
  2. Sends them + user's instruction to MiniMax M3
  3. Parses new file set
  4. Returns diff (what changed)
  5. Caller saves new version (Time Travel) and re-deploys preview
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from bot.services.llm import chat
from bot.services.site_generator import _FENCE_RE, _parse_response, GeneratedFile

logger = logging.getLogger(__name__)


AGENT_SYSTEM_PROMPT = """\
You are Buildo Agent, an expert front-end engineer that edits existing React+Vite projects.

The user has a working site. They will give you a natural-language instruction in Russian or English.
Your job: produce the COMPLETE new set of files (not a diff, not a patch) that reflects their change.

OUTPUT FORMAT (strict, JSON only, no markdown fences):
{
  "files": [
    {"path": "package.json", "content": "..."},
    {"path": "src/App.jsx", "content": "..."},
    ...
  ],
  "summary": "one-line description of what you changed",
  "preview_message": "short user-facing message explaining the change (in same language as user)"
}

RULES:
- Return ALL files, even unchanged ones. The caller uses the full set to rebuild.
- Preserve the existing design language (colors, fonts, layout) unless user asks to change them.
- If user asks for a color change, update CSS variables AND the relevant component classes.
- If user asks to add a section, add it to App.jsx and add CSS for it.
- Keep total file count <= 12. Merge components if needed.
- No placeholders, no TODOs, no "..." in code.
- For Russian: write content in Russian. For English: English.
- No emoji as icons. Use lucide-react (already in package.json) or hand-rolled SVGs.
"""


@dataclass
class AgentEdit:
    """Result of a single agent edit."""

    new_files: list[GeneratedFile] = field(default_factory=list)
    summary: str = ""
    preview_message: str = ""
    raw_response: str = ""


async def apply_edit(
    current_files: list[dict[str, str]],
    user_instruction: str,
    *,
    max_tokens: int = 16000,
) -> AgentEdit:
    """Apply a user instruction to an existing site.

    Args:
        current_files: All current files as [{path, content}, ...]
        user_instruction: User's natural-language edit request
        max_tokens: LLM response limit

    Returns:
        AgentEdit with new_files (full set), summary, preview_message
    """
    if not current_files:
        raise ValueError("current_files is empty — nothing to edit")
    if not user_instruction or not user_instruction.strip():
        raise ValueError("user_instruction is empty")

    # Compress current files to fit in context (skip package.json — usually unchanged)
    files_for_llm = [
        {"path": f["path"], "content": f["content"]}
        for f in current_files
        if not f["path"].endswith("package.json")
        and not f["path"].endswith("package-lock.json")
        and "node_modules" not in f["path"]
    ]

    user_msg = f"""\
Current project files (JSON):
```json
{json.dumps(files_for_llm, ensure_ascii=False)[:60000]}
```

User instruction: {user_instruction.strip()}

Return the COMPLETE updated file set as JSON. Include unchanged files too.
"""
    messages = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    logger.info(
        "agent.edit.start instruction_len=%d files=%d",
        len(user_instruction),
        len(files_for_llm),
    )

    raw = await chat(messages, max_tokens=max_tokens, temperature=0.5)
    logger.info("agent.edit.response_len=%d", len(raw))

    # Parse: we expect {files, summary, preview_message}
    parsed: dict[str, Any] | None = None
    text = raw.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        m = _FENCE_RE.search(text)
        if m:
            try:
                parsed = json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        if parsed is None:
            first = text.find("{")
            last = text.rfind("}")
            if first != -1 and last > first:
                try:
                    parsed = json.loads(text[first : last + 1])
                except json.JSONDecodeError:
                    pass

    if not isinstance(parsed, dict):
        # Fallback: try parsing as GeneratedSite (without preview_message)
        try:
            site = _parse_response(raw)
            return AgentEdit(
                new_files=site.files,
                summary=site.preview_summary or "Edit applied",
                preview_message="Сайт обновлён.",
                raw_response=raw,
            )
        except Exception:
            raise ValueError(f"Agent response is not valid JSON: {raw[:200]}")

    files_data = parsed.get("files", [])
    if not isinstance(files_data, list) or not files_data:
        raise ValueError("Agent response missing 'files' array")

    new_files = [
        GeneratedFile(path=str(f["path"]), content=str(f["content"]))
        for f in files_data
        if isinstance(f, dict) and "path" in f and "content" in f
    ]
    if not new_files:
        raise ValueError("No valid file entries in agent response")

    return AgentEdit(
        new_files=new_files,
        summary=str(parsed.get("summary", "")),
        preview_message=str(parsed.get("preview_message", "Сайт обновлён.")),
        raw_response=raw,
    )
