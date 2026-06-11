"""Site generator — converts user prompt to deployable site code.

Pipeline:
    user_prompt -> taste-skill system prompt -> MiniMax M3 (Anthropic-compat)
                -> parse code blocks -> {filename: content} dict
                -> save to disk as React/Vite project

This is the core of Buildo. Uses taste-skill v2 anti-slop design rules
loaded as system prompt so generated sites are not "AI-default ugly".
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from bot.services.llm import chat

logger = logging.getLogger(__name__)


# taste-skill v2 anti-slop + Vite+React stack instructions
# Loaded as system prompt for MiniMax M3
SITE_GENERATOR_SYSTEM_PROMPT = """\
You are Buildo, an elite front-end engineer who designs AND ships landing pages.

OUTPUT FORMAT (strict, non-negotiable):
- Reply with a JSON object (no prose, no markdown fences) of shape:
  {
    "project_name": "kebab-case-name",
    "framework": "vite-react",
    "files": [
      {"path": "package.json", "content": "..."},
      {"path": "index.html", "content": "..."},
      {"path": "src/main.jsx", "content": "..."},
      {"path": "src/App.jsx", "content": "..."},
      {"path": "src/index.css", "content": "..."},
      {"path": "src/components/Hero.jsx", "content": "..."}
    ],
    "preview_summary": "one-line description of what was built"
  }
- No text outside the JSON. No markdown fences. The JSON must parse.

DESIGN PRINCIPLES (taste-skill v2):
- Read the user's brief. Decide page kind (landing, portfolio, product) and audience.
- Set three dials mentally: VARIANCE 6-8 (asymmetric layouts ok), MOTION 3-5 (subtle scroll), DENSITY 3-4 (not air-gappy, not crowded).
- Anti-default: NO purple-to-blue gradients, NO centered "build the future" hero, NO Inter for everything, NO three-equal-feature-cards, NO glassmorphism everywhere.
- Pick a font pair that fits: Serif display (Fraunces/Playfair) + clean sans (Inter/JetBrains Mono), OR editorial (Lora + Work Sans), OR brutalist (Space Grotesk + IBM Plex Mono).
- Use a real palette: 2-3 primary colors + paper white + deep ink. NOT a SaaS-purple mess.
- Hierarchy: ONE primary headline, real subhead, proof points, CTA, footer.
- Mobile-first responsive. Use CSS grid and clamp() for type scale.
- Use real content, not Lorem Ipsum. Russian or English to match user.
- No emoji as icons. Use lucide-react or hand-rolled SVGs.

STACK:
- Vite 5 + React 18 + plain CSS (no Tailwind by default; Tailwind only if user asks).
- No TypeScript (keeps generated code simple to read and edit).
- Package.json scripts: dev, build, preview.
- Include a `vercel.json` rewrite for SPA (all -> /index.html).

QUALITY BAR:
- Code must RUN. `npm install && npm run build` must succeed.
- No placeholder comments. No TODO. No "..." truncations.
- Single-page only. Multi-page sites are out of scope for one call.
- Max 8 files per generation. If you need more, merge components.
"""


@dataclass
class GeneratedFile:
    """One file in the generated project."""

    path: str
    content: str


@dataclass
class GeneratedSite:
    """A complete generated site ready to deploy."""

    project_name: str
    framework: str
    files: list[GeneratedFile] = field(default_factory=list)
    preview_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "framework": self.framework,
            "files": [{"path": f.path, "content": f.content} for f in self.files],
            "preview_summary": self.preview_summary,
        }

    @property
    def total_size_kb(self) -> float:
        return sum(len(f.content.encode("utf-8")) for f in self.files) / 1024


# Heuristic fallback if MiniMax returns non-JSON (rare but happens)
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_response(raw: str) -> GeneratedSite:
    """Parse MiniMax response into GeneratedSite. Robust to markdown fences."""
    # Try direct JSON first
    text = raw.strip()
    parsed: dict | None = None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown fence
        m = _FENCE_RE.search(text)
        if m:
            try:
                parsed = json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # Try to find first { and last }
        if parsed is None:
            first = text.find("{")
            last = text.rfind("}")
            if first != -1 and last != -1 and last > first:
                try:
                    parsed = json.loads(text[first : last + 1])
                except json.JSONDecodeError:
                    pass

    if not isinstance(parsed, dict):
        raise ValueError(f"LLM response is not valid JSON object: {raw[:200]}")

    files_data = parsed.get("files", [])
    if not isinstance(files_data, list) or not files_data:
        raise ValueError("LLM response missing 'files' array")

    files = [
        GeneratedFile(path=str(f["path"]), content=str(f["content"]))
        for f in files_data
        if isinstance(f, dict) and "path" in f and "content" in f
    ]
    if not files:
        raise ValueError("No valid file entries in LLM response")

    return GeneratedSite(
        project_name=str(parsed.get("project_name", "buildo-site")),
        framework=str(parsed.get("framework", "vite-react")),
        files=files,
        preview_summary=str(parsed.get("preview_summary", "")),
    )


async def generate_site(prompt: str, *, max_tokens: int = 16000) -> GeneratedSite:
    """Generate a complete deployable site from a user prompt.

    Args:
        prompt: User's natural-language description of the site.
        max_tokens: Token limit for LLM response. Default 16k is enough
                    for a complete Vite+React project (5-8 files).

    Returns:
        GeneratedSite with all files ready to write to disk.

    Raises:
        ValueError: If LLM response cannot be parsed.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt is empty")

    messages = [
        {"role": "system", "content": SITE_GENERATOR_SYSTEM_PROMPT},
        {"role": "user", "content": prompt.strip()},
    ]
    logger.info("site.gen.start prompt_len=%d", len(prompt))

    raw = await chat(messages, max_tokens=max_tokens, temperature=0.7)
    site = _parse_response(raw)
    logger.info(
        "site.gen.ok project=%s files=%d size=%.1fKB",
        site.project_name,
        len(site.files),
        site.total_size_kb,
    )
    return site
