"""LLM service — wrapper around OpenRouter / MiniMax with model chain + fallback.

Primary chain (default):
    1. OR/minimax-m3
    2. OR/kimi-k2.6:free
    3. opencode-1/2/3

Image gen chain (separate, see article generation pipeline):
    OR/gemini-3.1-flash-image-preview × 2 keys

This module exposes a single async function `chat()` that runs the chain
on errors. Each step has its own timeout and retry budget.

NOTE: The user's claimed `sk-cp-...` key on `platform.minimax.io` returned
404 on 4 different URL patterns — that key is NOT integrated here. Real
key goes via env `LLM_API_KEY` and `OPENROUTER_API_KEY` (OpenRouter is
the working path).
"""

from __future__ import annotations

import logging

import httpx

from bot.config import get_settings


logger = logging.getLogger(__name__)

# Primary LLM chain (OpenRouter)
_PRIMARY_CHAIN: list[tuple[str, str]] = [
    ("openrouter", "minimax-m3"),
    ("openrouter", "kimi-k2.6:free"),
    ("openrouter", "opencode-1"),
    ("openrouter", "opencode-2"),
    ("openrouter", "opencode-3"),
]


class LLMError(Exception):
    """Raised when all LLM providers in the chain fail."""


async def _call_openrouter(model: str, messages: list[dict], max_tokens: int) -> str:
    settings = get_settings()
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def chat(
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    *,
    temperature: float = 0.7,
) -> str:
    """Run a chat completion through the LLM chain.

    Tries each model in `_PRIMARY_CHAIN` in order. Raises `LLMError`
    if all fail. Logs every attempt for ops visibility.
    """

    last_error: Exception | None = None

    for provider, model in _PRIMARY_CHAIN:
        try:
            logger.info("llm.try provider=%s model=%s", provider, model)
            if provider == "openrouter":
                return await _call_openrouter(model, messages, max_tokens)
            raise LLMError(f"unknown provider: {provider}")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("llm.fail provider=%s model=%s err=%s", provider, model, exc)
            continue

    raise LLMError(f"all LLM providers failed; last_error={last_error}")


async def generate_image(prompt: str, *, size: str = "1024x1024") -> str:
    """Stub for image generation. Returns empty string in MVP.

    Real implementation (Phase 1.5) will hit OpenRouter's
    google/gemini-3.1-flash-image-preview chain.
    """
    _ = (prompt, size)
    logger.info("image.gen (stub) prompt_len=%d", len(prompt))
    return ""
