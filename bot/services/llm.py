"""LLM service — multi-provider client.

Primary provider (2026-06-13): OpenRouter `nex-agi/nex-n2-pro:free`.
  - This is the user's chosen model, free tier, reasoning-capable.
  - We always set `reasoning.effort=minimal` for fast UX (0.8-3s typical).
  - We always set temperature=0.4 for predictable JSON output.
  - Streaming disabled (we need the full response for JSON parsing).
  - Uses OpenAI-compatible wire format: POST /api/v1/chat/completions.

Fallback provider: MiniMax M3 (`api.minimaxi.chat/anthropic/v1/messages`).
  - Anthropic-compatible wire format.
  - Used if OR returns 429/5xx after 2 retries.

Both providers go through the same `chat()` interface (system + user messages
→ string response), so `site_generator.py`, `quality.py`, admin-edit all
work transparently.

Why we DON'T use raw Anthropic-format for OR:
  - OpenRouter normalizes to OpenAI format on the wire.
  - Sending Anthropic `messages` body to OpenRouter returns 400.
  - OpenAI format works for BOTH free and paid models on OR.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from bot.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

# OpenRouter — primary (user-mandated 2026-06-13)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "nex-agi/nex-n2-pro:free"
OPENROUTER_REFERER = "https://buildo.bot"

# MiniMax — fallback (Anthropic-compatible)
BUILDO_LLM_URL = "https://api.minimaxi.chat/anthropic/v1/messages"
BUILDO_LLM_MODEL = "MiniMax-Text-01"


class LLMError(Exception):
    """Raised when both providers fail after retries."""


# ---------------------------------------------------------------------------
# OpenAI-format helpers (for OpenRouter)
# ---------------------------------------------------------------------------


def _build_openai_body(
    system_text: str | None,
    user_messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    model: str,
) -> dict[str, Any]:
    """Build OpenAI-compatible request body for OpenRouter."""
    messages: list[dict[str, str]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.extend(user_messages)
    return {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        # Minimal reasoning effort — keeps response time under 3s.
        # Free model still uses some tokens for reasoning, but minimal
        # is enough for site-generation.
        "reasoning": {"effort": "minimal"},
        # Disable streaming — we need the full response for JSON parsing.
        "stream": False,
    }


async def _call_openrouter(
    api_key: str,
    system_text: str | None,
    user_messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
) -> str:
    """Call OpenRouter via OpenAI-compatible endpoint. Returns text."""
    body = _build_openai_body(
        system_text, user_messages, max_tokens, temperature, OPENROUTER_MODEL
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_REFERER,
        "X-Title": "Buildo",
    }
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(OPENROUTER_URL, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        text = (msg.get("content") or "").strip()
        if not text:
            # Some reasoning models return content in `reasoning` field
            # if they ran out of tokens. Surface a clear error.
            raise LLMError(
                f"empty content from OR; finish_reason={choice.get('finish_reason')}; "
                f"reasoning_tokens={data.get('usage', {}).get('completion_tokens_details', {}).get('reasoning_tokens')}"
            )
        usage = data.get("usage", {})
        details = usage.get("completion_tokens_details", {})
        logger.info(
            "llm.openrouter.ok model=%s provider=%s in=%s out=%s reasoning=%s",
            data.get("model"),
            data.get("provider"),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            details.get("reasoning_tokens"),
        )
        return text


# ---------------------------------------------------------------------------
# Anthropic-format helpers (for MiniMax fallback)
# ---------------------------------------------------------------------------


def _convert_messages_to_anthropic(
    messages: list[dict[str, str]],
) -> tuple[str | None, list[dict[str, Any]]]:
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


async def _call_minimax(
    api_key: str,
    system_text: str | None,
    user_messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
) -> str:
    """Call MiniMax M3 via Anthropic-compatible endpoint. Returns text."""
    body: dict[str, Any] = {
        "model": BUILDO_LLM_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": user_messages,
    }
    if system_text:
        body["system"] = system_text
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(BUILDO_LLM_URL, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        content_blocks = data.get("content", [])
        text_parts = [
            block.get("text", "")
            for block in content_blocks
            if block.get("type") == "text"
        ]
        result = "".join(text_parts).strip()
        if not result:
            raise LLMError(f"empty response: {data}")
        return result


# ---------------------------------------------------------------------------
# Public chat() — primary OpenRouter, fallback MiniMax
# ---------------------------------------------------------------------------


# Keys are loaded fresh per-call so config reloads work
async def chat(
    messages: list[dict[str, str]],
    max_tokens: int = 4096,
    *,
    temperature: float = 0.4,
) -> str:
    """Call LLM with primary (OpenRouter nex-agi) + fallback (MiniMax M3).

    Returns the assistant's text response. Raises LLMError if both fail.
    """
    settings = get_settings()

    # Split system from user/assistant for clean provider routing
    system_text: str | None = None
    user_messages: list[dict[str, str]] = []
    for m in messages:
        if m.get("role") == "system":
            system_text = (system_text + "\n\n" if system_text else "") + m["content"]
        else:
            user_messages.append(m)

    # --- Primary: OpenRouter ---
    or_key = settings.openrouter_api_key
    if or_key and or_key != "dummy_or_key":
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                return await _call_openrouter(
                    or_key, system_text, user_messages, max_tokens, temperature
                )
            except httpx.HTTPStatusError as e:
                # 4xx (except 429) is hard fail — no retry
                if e.response.status_code not in (429, 500, 502, 503, 504):
                    logger.warning(
                        "llm.openrouter.hard_fail status=%s body=%s",
                        e.response.status_code,
                        e.response.text[:300],
                    )
                    break
                last_err = e
                logger.warning(
                    "llm.openrouter.transient status=%s retry=%d",
                    e.response.status_code,
                    attempt + 1,
                )
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning("llm.openrouter.error err=%s retry=%d", e, attempt + 1)
            import asyncio

            await asyncio.sleep(1.5 * (attempt + 1))
        logger.warning(
            "llm.openrouter.exhausted err=%s — falling back to MiniMax", last_err
        )
    else:
        logger.info("OPENROUTER_API_KEY not set — using MiniMax directly")

    # --- Fallback: MiniMax M3 ---
    m3_key = settings.llm_api_key
    if not m3_key or m3_key == "dummy_llm_key":
        raise LLMError("Neither OPENROUTER_API_KEY nor LLM_API_KEY configured")

    last_err = None
    for attempt in range(3):
        try:
            return await _call_minimax(
                m3_key, system_text, user_messages, max_tokens, temperature
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            import asyncio

            await asyncio.sleep(2**attempt)
    raise LLMError(f"all providers failed; last_error={last_err}")


async def generate_image(prompt: str, *, size: str = "1024x1024") -> str:
    """Stub. Image-gen chain lands in Phase 1.5 (OR/gemini-3.1-flash-image-preview)."""
    _ = (prompt, size)
    return ""
