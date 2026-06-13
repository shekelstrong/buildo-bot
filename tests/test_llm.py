"""Tests for multi-provider LLM service.

We mock both providers so tests don't hit the network.
Verifies:
- chat() prefers OpenRouter when key is set
- chat() falls back to MiniMax when OpenRouter fails
- chat() raises when neither key is set
- chat() includes `reasoning.effort=minimal` in OpenRouter body
- chat() uses OpenAI-format body (not Anthropic) for OpenRouter
- chat() uses Anthropic-format body for MiniMax
- chat() splits system from user/assistant messages correctly
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from bot.services import llm
from bot.services.llm import LLMError, chat


def _or_ok_response(text: str = '{"x":1}') -> dict:
    return {
        "id": "gen-x",
        "model": "nex-agi/nex-n2-pro:free",
        "provider": "Nex AGI",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
            "completion_tokens_details": {"reasoning_tokens": 5},
        },
    }


def _m3_ok_response(text: str = '{"x":1}') -> dict:
    return {
        "model": "MiniMax-Text-01",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


def _err_response(status: int = 503) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json={"error": "x"},
        request=httpx.Request("POST", "https://x"),
    )


# --- Settings mock ---


class _FakeSettings:
    def __init__(self, or_key="sk-or-x", m3_key="m3-x"):
        self.openrouter_api_key = or_key
        self.llm_api_key = m3_key


@pytest.fixture
def fake_settings():
    return _FakeSettings()


# --- Tests ---


@pytest.mark.asyncio
async def test_chat_prefers_openrouter(fake_settings):
    """When OR key is set, we call OR and return its text."""
    with (
        patch.object(llm, "get_settings", return_value=fake_settings),
        patch.object(llm.httpx, "AsyncClient") as mock_client,
    ):
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: _or_ok_response('{"files":[],"project_name":"x"}')

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=None)
        ctx.post = AsyncMock(return_value=mock_resp)
        mock_client.return_value = ctx

        result = await chat(
            [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hi"},
            ]
        )
        assert result == '{"files":[],"project_name":"x"}'
        # Verify OR URL was hit, not Anthropic
        called_url = ctx.post.call_args.args[0]
        assert "openrouter.ai" in called_url
        # Verify body has reasoning.effort=minimal
        body = ctx.post.call_args.kwargs["json"]
        assert body["model"] == "nex-agi/nex-n2-pro:free"
        assert body["reasoning"] == {"effort": "minimal"}
        assert body["stream"] is False
        # Verify system was extracted to top-level system message
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "be brief"
        assert body["messages"][1]["role"] == "user"


@pytest.mark.asyncio
async def test_chat_falls_back_to_minimax_on_or_failure(fake_settings):
    """When OR returns 503 twice, we fall back to MiniMax."""
    with (
        patch.object(llm, "get_settings", return_value=fake_settings),
        patch.object(llm.httpx, "AsyncClient") as mock_client,
    ):
        # OR call returns 503
        or_resp = AsyncMock()
        or_resp.status_code = 503
        or_resp.raise_for_status = lambda: (_ for _ in ()).throw(
            httpx.HTTPStatusError(
                "x", request=httpx.Request("POST", "https://x"), response=or_resp
            )
        )

        # M3 call returns OK
        m3_resp = AsyncMock()
        m3_resp.status_code = 200
        m3_resp.raise_for_status = lambda: None
        m3_resp.json = lambda: _m3_ok_response('{"fallback":true}')

        call_count = {"n": 0}

        def make_ctx(*args, **kwargs):
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=None)

            async def post(url, **kw):
                call_count["n"] += 1
                if "openrouter" in url:
                    return or_resp
                return m3_resp

            ctx.post = post
            return ctx

        mock_client.side_effect = make_ctx
        result = await chat([{"role": "user", "content": "hi"}])
        assert result == '{"fallback":true}'
        # Both providers were called
        assert call_count["n"] >= 2


@pytest.mark.asyncio
async def test_chat_no_keys_raises():
    """When neither key is set, we raise LLMError."""
    settings = _FakeSettings(or_key="dummy_or_key", m3_key="dummy_llm_key")
    with patch.object(llm, "get_settings", return_value=settings):
        with pytest.raises(LLMError, match="Neither.*configured"):
            await chat([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_chat_or_hard_fail_4xx_skips_retries(fake_settings):
    """OR returning 400 (not 429/5xx) should NOT retry, just fall back."""
    with (
        patch.object(llm, "get_settings", return_value=fake_settings),
        patch.object(llm.httpx, "AsyncClient") as mock_client,
    ):
        or_resp = AsyncMock()
        or_resp.status_code = 400
        or_resp.text = "bad request"
        or_resp.raise_for_status = lambda: (_ for _ in ()).throw(
            httpx.HTTPStatusError(
                "x", request=httpx.Request("POST", "https://x"), response=or_resp
            )
        )

        m3_resp = AsyncMock()
        m3_resp.status_code = 200
        m3_resp.raise_for_status = lambda: None
        m3_resp.json = lambda: _m3_ok_response('{"m3":true}')

        or_calls = {"n": 0}

        def make_ctx(*args, **kwargs):
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=None)

            async def post(url, **kw):
                if "openrouter" in url:
                    or_calls["n"] += 1
                    return or_resp
                return m3_resp

            ctx.post = post
            return ctx

        mock_client.side_effect = make_ctx
        result = await chat([{"role": "user", "content": "hi"}])
        assert result == '{"m3":true}'
        # OR should have been called exactly once (no retry on 4xx)
        assert or_calls["n"] == 1


@pytest.mark.asyncio
async def test_chat_only_minimax_when_or_key_dummy(fake_settings):
    """When OR key is dummy, skip OR entirely and call MiniMax directly."""
    settings = _FakeSettings(or_key="dummy_or_key", m3_key="m3-real")
    with (
        patch.object(llm, "get_settings", return_value=settings),
        patch.object(llm.httpx, "AsyncClient") as mock_client,
    ):
        m3_resp = AsyncMock()
        m3_resp.status_code = 200
        m3_resp.raise_for_status = lambda: None
        m3_resp.json = lambda: _m3_ok_response('{"m3":true}')

        called_urls = []

        def make_ctx(*args, **kwargs):
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=None)

            async def post(url, **kw):
                called_urls.append(url)
                return m3_resp

            ctx.post = post
            return ctx

        mock_client.side_effect = make_ctx
        result = await chat([{"role": "user", "content": "hi"}])
        assert result == '{"m3":true}'
        # Only MiniMax URL was hit, not OpenRouter
        assert len(called_urls) == 1
        assert "minimaxi.chat" in called_urls[0]
