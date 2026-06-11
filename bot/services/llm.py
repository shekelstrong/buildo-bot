"""LLM service — direct MiniMax M3 client (Anthropic-compatible).

Primary provider: api.minimaxi.chat (user-provided key sk-cp-...).
Model: MiniMax-Text-01.
Endpoint: https://api.minimaxi.chat/anthropic/v1/messages (Anthropic-compatible)
Fallback: 3x retry with exponential backoff, then 503 to caller.

This is the SAME model running Hermes Agent itself (per user mandate
"ровно та же модель, которая сейчас работает в Hermes Agent").
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from bot.config import get_settings

logger = logging.getLogger(__name__)

# Only path that works with user-provided key on 2026-06-11 testing.
# Other endpoints (platform.minimax.io, text-anthropic-api) returned 404.
BUILDO_LLM_URL = "https://api.minimaxi.chat/anthropic/v1/messages"
BUILDO_LLM_MODEL = "MiniMax-Text-01"


class LLMError(Exception):
    """Raised when LLM provider fails after retries."""


def _convert_messages_to_anthropic(
    messages: list[dict[str, str]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert OpenAI-style {role, content} list to Anthropic format.

    Anthropic requires system message separate and messages without
    system role.
    """
    system_text: str | None = None
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            system_text = (system_text + "\n\n" if system_text else "") + content
        elif role in ("user", "assistant"):
            out.append({"role": role, "content": content})
    if not out:
        out = [{"role": "user", "content": ""}]
    return system_text, out


async def chat(
    messages: list[dict[str, str]],
    max_tokens: int = 4096,
    *,
    temperature: float = 0.7,
) -> str:
    """Call MiniMax M3 via Anthropic-compatible endpoint.

    Same model Hermes Agent itself uses. Retries 3x with backoff on
    transient errors. Raises LLMError on hard failure.
    """
    settings = get_settings()
    api_key = settings.llm_api_key
    if not api_key or api_key == "dummy_llm_key":
        raise LLMError("LLM_API_KEY not configured in .env")

    system_text, anthropic_messages = _convert_messages_to_anthropic(messages)
    body: dict[str, Any] = {
        "model": BUILDO_LLM_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": anthropic_messages,
    }
    if system_text:
        body["system"] = system_text

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(BUILDO_LLM_URL, headers=headers, json=body)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        "transient", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                data = resp.json()
                # Anthropic format: content[].text
                content_blocks = data.get("content", [])
                text_parts = [
                    block.get("text", "")
                    for block in content_blocks
                    if block.get("type") == "text"
                ]
                result = "".join(text_parts).strip()
                if not result:
                    raise LLMError(f"empty response: {data}")
                logger.info(
                    "llm.ok model=%s in_tokens=%s out_tokens=%s",
                    data.get("model", "?"),
                    data.get("usage", {}).get("input_tokens", "?"),
                    data.get("usage", {}).get("output_tokens", "?"),
                )
                return result
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            wait = 2**attempt
            logger.warning(
                "llm.attempt=%d failed err=%s retry_in=%ds",
                attempt + 1,
                exc,
                wait,
            )
            import asyncio

            await asyncio.sleep(wait)

    raise LLMError(f"all retries exhausted; last_error={last_error}")


async def generate_image(prompt: str, *, size: str = "1024x1024") -> str:
    """Stub. Image-gen chain lands in Phase 1.5 (OR/gemini-3.1-flash-image-preview)."""
    _ = (prompt, size)
    return ""
