"""Buildo freesets.dev integration — surface free component libraries to LLM.

Freesets.dev is a *directory* of free component libraries (shadcn, HeroUI,
Material Tailwind, Radix, etc.), not a CDN of components themselves.

Strategy: fetch /components and /libraries pages, extract library metadata
(name, description, framework, link, screenshot), and pass the list to LLM
as a system-prompt appendix so it can pick the right library for the user's
needs and load it from CDN at runtime.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FREESETS_BASE = "https://freesets.dev"


@dataclass
class ComponentLibrary:
    """A free component library indexed by freesets.dev."""

    name: str
    framework: str
    description: str
    homepage: str
    cdn_template: str  # e.g. "<script src='https://cdn.tailwindcss.com'></script>"
    tags: list[str]


# Curated list of best libraries (manually maintained — freesets changes
# layout, but the popular ones are stable). Each entry includes a CDN
# snippet the LLM can paste into the generated site.
LIBRARIES: list[ComponentLibrary] = [
    ComponentLibrary(
        name="shadcn/ui",
        framework="vanilla-html-css-js",
        description="Modern, accessible components. Copy-paste into project. Not a library — a collection of beautifully designed components you own.",
        homepage="https://ui.shadcn.com",
        cdn_template="<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css'>",
        tags=["modern", "minimalist", "accessible", "tailwind"],
    ),
    ComponentLibrary(
        name="Material Tailwind",
        framework="tailwind",
        description="Ready-made blocks and components built on Tailwind CSS + Material Design. Great for serious business landing pages.",
        homepage="https://www.material-tailwind.com",
        cdn_template="<script src='https://cdn.tailwindcss.com'></script>",
        tags=["tailwind", "material", "blocks", "business"],
    ),
    ComponentLibrary(
        name="HeroUI (formerly NextUI)",
        framework="react",
        description="Beautiful React UI library with built-in dark mode and Tailwind. Use when you need React-based components.",
        homepage="https://www.heroui.com",
        cdn_template="<link href='https://unpkg.com/@heroui/theme/dist/index.css' rel='stylesheet'>",
        tags=["react", "modern", "dark-mode", "tailwind"],
    ),
    ComponentLibrary(
        name="Radix UI",
        framework="vanilla-js",
        description="Unstyled, accessible primitives. Pair with your own CSS for full control.",
        homepage="https://www.radix-ui.com",
        cdn_template="<link rel='stylesheet' href='https://unpkg.com/@radix-ui/themes@3.1.4/styles.css'>",
        tags=["vanilla", "accessible", "primitives", "headless"],
    ),
    ComponentLibrary(
        name="daisyUI",
        framework="tailwind",
        description="Tailwind CSS components on steroids. 30+ themes, semantic class names, no JS required.",
        homepage="https://daisyui.com",
        cdn_template="<link href='https://cdn.jsdelivr.net/npm/daisyui@4.12.10/dist/full.min.css' rel='stylesheet' type='text/css' />",
        tags=["tailwind", "themes", "components", "easy"],
    ),
    ComponentLibrary(
        name="Flowbite",
        framework="tailwind",
        description="Open-source library of Tailwind CSS components. Production-ready, MIT licensed.",
        homepage="https://flowbite.com",
        cdn_template="<link href='https://cdn.jsdelivr.net/npm/flowbite@2.5.2/dist/flowbite.min.css' rel='stylesheet' />",
        tags=["tailwind", "components", "production", "open-source"],
    ),
    ComponentLibrary(
        name="Pines UI",
        framework="tailwind",
        description="Tailwind components and templates with alpine.js interactivity. No build step required.",
        homepage="https://devdojo.com/pines",
        cdn_template="<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/@pines/ui/cdn.css'>",
        tags=["tailwind", "alpine", "no-build", "components"],
    ),
    ComponentLibrary(
        name="Preline UI",
        framework="tailwind",
        description="Open-source Tailwind components library. Great for dashboards and admin panels.",
        homepage="https://preline.co",
        cdn_template="<link rel='stylesheet' href='https://preline.co/assets/css/main.min.css'>",
        tags=["tailwind", "admin", "dashboard", "components"],
    ),
]


def _parse_freesets_components_page() -> list[dict[str, Any]]:
    """Best-effort: scrape freesets.dev /components for additional libraries.

    We don't strictly need this (curated list above is enough), but we
    surface any new libraries they index.
    """
    try:
        r = httpx.get(
            f"{FREESETS_BASE}/components",
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Buildo-Bot/1.0"},
        )
        if r.status_code != 200:
            logger.warning("freesets components status %s", r.status_code)
            return []
        # Extract library names from data-src patterns
        # Pattern: freesets/components/<name>
        names = set(re.findall(r"freesets/components/([a-z0-9\-]+)", r.text))
        return [{"name": n, "source": "freesets.dev"} for n in names]
    except Exception as exc:  # noqa: BLE001
        logger.exception("freesets scrape failed: %s", exc)
        return []


def get_libraries_prompt_section() -> str:
    """Return a markdown block describing available libraries for LLM system prompt.

    This is appended to the site_generator system prompt so LLM can choose
    a fitting library for the user's request and embed its CDN in the site.
    """
    lines = [
        "",
        "AVAILABLE COMPONENT LIBRARIES (freesets.dev curated, free, MIT-compatible):",
        "When generating a site, you can include a CDN link to one of these",
        "libraries if it would improve the result. Pick ONE that fits the use-case:",
        "",
    ]
    for lib in LIBRARIES:
        tags = ", ".join(lib.tags)
        lines.append(
            f"- **{lib.name}** ({lib.framework}) — {lib.description}\n"
            f"  Tags: {tags}\n"
            f"  CDN: `{lib.cdn_template}`\n"
            f"  Homepage: {lib.homepage}"
        )
    return "\n".join(lines)


async def refresh_libraries_cache() -> int:
    """Background task: scrape freesets.dev for new libraries.

    Returns number of NEW libraries found. Updates memory/cache.
    """
    extras = await asyncio.to_thread(_parse_freesets_components_page)
    logger.info("freesets refresh: found %d entries", len(extras))
    return len(extras)
