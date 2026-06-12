"""Site generator — converts user prompt to deployable PURE STATIC site code.

Pipeline:
    user_prompt -> taste-skill system prompt -> MiniMax M3 (Anthropic-compat)
                -> parse code blocks -> {filename: content} dict
                -> save to disk as HTML/CSS/JS (no build step required)

This is the core of Buildo. Uses taste-skill v2 anti-slop design rules
loaded as system prompt so generated sites are not "AI-default ugly".

Why pure static (no Vite, no React, no npm):
- Zero build step: no Node.js in container, no 90s npm install
- Deploys in <5s, runs on Layero, GitHub Pages, anywhere
- 1 file (index.html with inline CSS/JS) is easier to edit via AI agent
- Future-proof: works even on cheapest static hosts
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from bot.services.freesets import get_libraries_prompt_section
from bot.services.llm import chat

logger = logging.getLogger(__name__)


# taste-skill v2 anti-slop + PURE STATIC stack (no build step)
SITE_GENERATOR_SYSTEM_PROMPT = """\
You are Buildo, an elite front-end engineer who designs AND ships landing pages.

OUTPUT FORMAT (strict, non-negotiable):
- Reply with a JSON object (no prose, no markdown fences) of shape:
  {
    "project_name": "kebab-case-name",
    "framework": "static-html",
    "files": [
      {"path": "index.html", "content": "..."}
    ],
    "preview_summary": "one-line description of what was built"
  }
- No text outside the JSON. No markdown fences. The JSON must parse.
- ALWAYS output a SINGLE file: index.html with inline <style> and <script>.
  No external CSS files, no external JS files, no package.json.
  This is by design — instant deploy, zero build, AI-agent-friendly.

DESIGN PRINCIPLES (taste-skill v2):
- Read the user's brief. Decide page kind (landing, portfolio, product) and audience.
- Set three dials mentally: VARIANCE 6-8 (asymmetric layouts ok), MOTION 3-5 (subtle scroll), DENSITY 3-4 (not air-gappy, not crowded).
- Anti-default: NO purple-to-blue gradients, NO centered "build the future" hero, NO Inter for everything, NO three-equal-feature-cards, NO glassmorphism everywhere.
- Pick a font pair that fits: Serif display (Fraunces/Playfair) + clean sans (Inter/JetBrains Mono), OR editorial (Lora + Work Sans), OR brutalist (Space Grotesk + IBM Plex Mono).
- Use a real palette: 2-3 primary colors + paper white + deep ink. NOT a SaaS-purple mess.
- Hierarchy: ONE primary headline, real subhead, proof points, CTA, footer.
- Mobile-first responsive. Use CSS grid and clamp() for type scale.
- Use real content, not Lorem Ipsum. Russian or English to match user.
- No emoji as icons. Use inline SVGs (lucide-style stroke icons).
- Use Google Fonts via @import in <style>. DO NOT use local font files.
- Add subtle scroll-reveal via IntersectionObserver (inline JS, no libraries).
- Make CTA buttons visually obvious (high contrast, hover state, scale on hover).

HTML STRUCTURE:
- For SIMPLE landing pages (one section, 3-5 components): SINGLE file — output ONE index.html with inline <style> and <script>.
- For SERIOUS projects (multi-section landing with interactivity, animations, multiple components, dark mode, forms with validation, etc.): use MULTI-FILE structure:
  - index.html — semantic markup with <link rel="stylesheet" href="styles.css"> and <script src="app.js"></script>
  - styles.css — all CSS extracted
  - app.js — all JS extracted (use vanilla JS — no jQuery, no frameworks)
  - Optional: assets/logo.svg for logos, assets/hero.jpg for images (just placeholder paths — we don't generate images, we just structure the code)
- Default: choose SINGLE-file for simple requests, MULTI-file for complex ones. Use your judgment.
- <head>: meta charset, viewport, title, <style> block or <link> to CSS, Google Fonts import
- <body>: header/nav (sticky), hero, features (asymmetric, not 3-equal-cards), proof/social-proof, CTA, footer
- Add basic SEO: <title>, <meta name="description">, og:title, og:description

QUALITY BAR:
- Code must RUN. Just open index.html in a browser — that's it.
- No placeholder comments. No TODO. No "..." truncations.
- Single-page only. Multi-page sites are out of scope for one call.
- File size: 20-40KB total (across all files). Less is fine. Be CONCISE.
- Use 3-5 sections max. Short copy. Tight CSS. No bloat.
""" + get_libraries_prompt_section()


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


def _extract_balanced_json_object(text: str, start: int = 0) -> tuple[str, int] | None:
    """Find a balanced {...} JSON object starting from `start` (or from the
    first '{' if start == 0). Returns (substring, end_index_exclusive) or None.

    Robust against quoted strings containing unmatched braces. Handles
    backslash-escaped quotes inside strings.
    """
    if start == 0:
        start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1], i + 1
        i += 1
    return None


def _parse_response(raw: str) -> GeneratedSite:
    """Parse MiniMax response into GeneratedSite. Robust to markdown fences,
    truncated JSON, and unescaped HTML inside content strings.
    """
    text = raw.strip()
    parsed: dict | None = None

    # 1) Direct JSON parse
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) Markdown-fenced JSON
    if parsed is None:
        m = _FENCE_RE.search(text)
        if m:
            try:
                parsed = json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

    # 3) Balanced-brace extraction (handles content with embedded braces)
    if parsed is None:
        candidate = _extract_balanced_json_object(text)
        if candidate is not None:
            candidate_str, _ = candidate
            try:
                parsed = json.loads(candidate_str)
            except json.JSONDecodeError:
                pass

    # 4) Repair truncated JSON (LLM hit max_tokens mid-string)
    if parsed is None and text.startswith("{"):
        repaired = text
        for _ in range(5):
            open_brackets = repaired.count("[") - repaired.count("]")
            open_braces = repaired.count("{") - repaired.count("}")
            last_quote = repaired.rfind('"')
            if last_quote > repaired.rfind("}"):
                repaired = repaired[:last_quote] + '"'
            repaired += "]" * max(0, open_brackets) + "}" * max(0, open_braces)
            try:
                parsed = json.loads(repaired)
                logger.warning(
                    "Repaired truncated JSON (%d extra chars appended)",
                    len(repaired) - len(text),
                )
                break
            except json.JSONDecodeError:
                break

    # 5) Last-resort heuristic: extract first <!DOCTYPE ...> or <html ...>
    # HTML blob and wrap it into a GeneratedSite. Saves the user when the
    # LLM returns pure HTML instead of a JSON envelope.
    if parsed is None or (isinstance(parsed, str) and "<" in parsed):
        # If json.loads already returned a quoted-HTML string, use it directly
        if isinstance(parsed, str):
            html = parsed
        else:
            html_match = re.search(
                r"<!doctype\s+html|<\?xml|<html\b", text, re.IGNORECASE
            )
            if not html_match:
                raise ValueError(f"LLM response is not valid JSON object: {raw[:200]}")
            html = text[html_match.start() :].strip()
            if html.startswith('"') and html.endswith('"'):
                try:
                    html = json.loads(html)
                except json.JSONDecodeError:
                    html = html[1:-1]
        html = html.replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'")
        if "<" in html:  # only accept if it really looks like HTML
            logger.warning(
                "Falling back to raw-HTML heuristic (%d chars) — LLM did not return JSON",
                len(html),
            )
            return GeneratedSite(
                project_name="generated-site",
                framework="static-html",
                files=[GeneratedFile(path="index.html", content=html)],
                preview_summary="",
            )
        # Not HTML after all — fall through to error
        raise ValueError(f"LLM response is not valid JSON object: {raw[:200]}")

    if not isinstance(parsed, dict):
        raise ValueError(f"LLM response is not a JSON object: {raw[:200]}")

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

    # Whitelist allowed paths (security + cleanliness)
    allowed_exts = (".html", ".css", ".js", ".json", ".svg", ".txt", ".xml")
    files = [f for f in files if f.path.lower().endswith(allowed_exts)]
    if not files:
        raise ValueError("No valid file types (allowed: html/css/js/json/svg/txt/xml)")

    # Sanitize paths: keep relative, no ../
    sanitized: list[GeneratedFile] = []
    for f in files:
        clean_path = f.path.lstrip("/")
        if ".." in clean_path.split("/"):
            continue
        sanitized.append(f)
    files = sanitized
    if not files:
        raise ValueError("No valid paths after sanitization")

    has_index = any(f.path == "index.html" for f in files)
    if not has_index:
        # If LLM gave us separate css/js but no index.html, build one
        css = "\n".join(f.content for f in files if f.path.endswith(".css"))
        js = "\n".join(f.content for f in files if f.path.endswith(".js"))
        html_body = next(
            (f.content for f in files if f.path.endswith(".html")),
            "<h1>Buildo site</h1>",
        )
        if "<body" in html_body:
            import re as _re

            body_match = _re.search(r"<body[^>]*>(.*?)</body>", html_body, _re.DOTALL)
            if body_match:
                html_body = body_match.group(1)

        merged = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Buildo</title>
  <style>{css}</style>
</head>
<body>{html_body}<script>{js}</script></body>
</html>"""
        files = [GeneratedFile(path="index.html", content=merged)]
    else:
        # Multi-file: ensure index.html has <link> tags for separate CSS files
        # and <script src> for separate JS files (rather than inline).
        idx_idx = next(i for i, f in enumerate(files) if f.path == "index.html")
        idx_content = files[idx_idx].content
        external_css = [
            f for f in files if f.path.endswith(".css") and f.path != "index.html"
        ]
        external_js = [
            f for f in files if f.path.endswith(".js") and f.path != "index.html"
        ]
        # If external files exist and not inlined, add <link>/<script src>
        if external_css and "</head>" in idx_content:
            links = "\n".join(
                f'  <link rel="stylesheet" href="{f.path}">' for f in external_css
            )
            idx_content = idx_content.replace("</head>", f"{links}\n</head>", 1)
        if external_js and "</body>" in idx_content:
            scripts = "\n".join(
                f'  <script src="{f.path}"></script>' for f in external_js
            )
            idx_content = idx_content.replace("</body>", f"{scripts}\n</body>", 1)
        files[idx_idx] = GeneratedFile(path="index.html", content=idx_content)

    return GeneratedSite(
        project_name=str(parsed.get("project_name", "buildo-site")),
        framework="static-html",
        files=files,
        preview_summary=str(parsed.get("preview_summary", "")),
    )


async def generate_site(prompt: str, *, max_tokens: int = 32000) -> GeneratedSite:
    """Generate a complete deployable static site from a user prompt.

    Args:
        prompt: User's natural-language description of the site.
        max_tokens: Token limit for LLM response. Default 32k is enough
                    for a single rich index.html with inline CSS/JS.

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
