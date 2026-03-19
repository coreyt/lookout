"""Tests for LiteLLMClient."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lookout_tui.clients.litellm_client import (
    LiteLLMClient,
    get_available_models,
    validate_model_env,
)
from lookout_tui.clients.protocol import LLMClient, LLMResponse

_MOD = "lookout_tui.clients.litellm_client"


def _mock_response(content: str = "OK", model: str = "test-model") -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.model = model
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    return resp


class TestLiteLLMClientPayload:
    async def test_chat_sends_correct_payload(self) -> None:
        client = LiteLLMClient(
            model="gemini/gemini-3.1-pro",
            temperature=0.5,
            max_tokens=1024,
            timeout=30.0,
            max_retries=2,
        )

        with patch(f"{_MOD}.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=_mock_response("Hello!"))
            await client.chat("You are helpful.", "Hi there")

        kw = mock_litellm.acompletion.call_args.kwargs
        assert kw["model"] == "gemini/gemini-3.1-pro"
        assert kw["temperature"] == 0.5
        assert kw["max_tokens"] == 1024
        assert kw["timeout"] == 30.0
        assert kw["num_retries"] == 0
        msgs = kw["messages"]
        assert len(msgs) == 2
        assert msgs[0] == {"role": "system", "content": "You are helpful."}
        assert msgs[1] == {"role": "user", "content": "Hi there"}

    async def test_chat_uses_x_airlock_client_header(self) -> None:
        client = LiteLLMClient(model="gemini/gemini-3.1-pro")
        with (
            patch.dict(os.environ, {"AIRLOCK_HOST": "localhost", "AIRLOCK_PORT": "4000", "AIRLOCK_CLIENT": "lookout-test"}, clear=False),
            patch(f"{_MOD}.litellm") as mock_litellm,
        ):
            mock_litellm.acompletion = AsyncMock(return_value=_mock_response("Hello!"))
            await client.chat("You are helpful.", "Hi there")

        kw = mock_litellm.acompletion.call_args.kwargs
        assert kw["api_base"] == "http://localhost:4000/v1"
        assert kw["extra_headers"]["X-Airlock-Client"] == "lookout-test"

    async def test_chat_returns_llm_response(self) -> None:
        client = LiteLLMClient(model="gemini/gemini-3.1-pro")

        resp = _mock_response("Generated text", "gemini-3.1-pro")
        resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50, total_tokens=150)

        with patch(f"{_MOD}.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=resp)
            result = await client.chat("sys", "user")

        assert isinstance(result, LLMResponse)
        assert result.content == "Generated text"
        assert result.model == "gemini-3.1-pro"
        assert result.usage["prompt_tokens"] == 100
        assert result.usage["completion_tokens"] == 50
        assert result.usage["total_tokens"] == 150

    async def test_raises_on_bad_response_format(self) -> None:
        client = LiteLLMClient(model="test-model")
        bad_resp = MagicMock()
        bad_resp.choices = []

        with patch(f"{_MOD}.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=bad_resp)
            with pytest.raises(RuntimeError, match="Unexpected response format"):
                await client.chat("sys", "user")

    async def test_handles_missing_usage(self) -> None:
        client = LiteLLMClient(model="test-model")
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="OK"))]
        resp.model = "test-model"
        resp.usage = None

        with patch(f"{_MOD}.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=resp)
            result = await client.chat("sys", "user")

        assert result.content == "OK"
        assert result.usage == {}


class TestLiteLLMClientRetry:
    async def test_retries_on_rate_limit(self) -> None:
        client = LiteLLMClient(model="test-model", max_retries=2)
        err_cls = type("RateLimitError", (Exception,), {})

        with (
            patch(f"{_MOD}.litellm") as mock_litellm,
            patch(f"{_MOD}.LiteLLMRateLimitError", err_cls),
            patch(f"{_MOD}.asyncio.sleep", new_callable=AsyncMock) as ms,
        ):
            mock_litellm.acompletion = AsyncMock(
                side_effect=[err_cls("rate limited"), _mock_response()]
            )
            result = await client.chat("sys", "user")
            assert result.content == "OK"
            ms.assert_called_once_with(1)

    async def test_retries_on_connection_error(self) -> None:
        client = LiteLLMClient(model="test-model", max_retries=2)
        err_cls = type("APIConnectionError", (Exception,), {})

        with (
            patch(f"{_MOD}.litellm") as mock_litellm,
            patch(f"{_MOD}.LiteLLMAPIConnectionError", err_cls),
            patch(f"{_MOD}.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_litellm.acompletion = AsyncMock(
                side_effect=[err_cls("conn err"), _mock_response()]
            )
            result = await client.chat("sys", "user")
            assert result.content == "OK"

    async def test_retries_on_timeout(self) -> None:
        client = LiteLLMClient(model="test-model", max_retries=2)
        err_cls = type("Timeout", (Exception,), {})

        with (
            patch(f"{_MOD}.litellm") as mock_litellm,
            patch(f"{_MOD}.LiteLLMTimeout", err_cls),
            patch(f"{_MOD}.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_litellm.acompletion = AsyncMock(side_effect=[err_cls("timeout"), _mock_response()])
            result = await client.chat("sys", "user")
            assert result.content == "OK"

    async def test_retry_exhaustion_raises(self) -> None:
        client = LiteLLMClient(model="test-model", max_retries=2)
        err_cls = type("RateLimitError", (Exception,), {})

        with (
            patch(f"{_MOD}.litellm") as mock_litellm,
            patch(f"{_MOD}.LiteLLMRateLimitError", err_cls),
            patch(f"{_MOD}.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(RuntimeError, match="after 2 attempts"),
        ):
            mock_litellm.acompletion = AsyncMock(
                side_effect=[err_cls("rate limited"), err_cls("rate limited")]
            )
            await client.chat("sys", "user")

    async def test_auth_error_not_retried(self) -> None:
        client = LiteLLMClient(model="test-model", max_retries=3)
        err_cls = type("AuthenticationError", (Exception,), {})

        with (
            patch(f"{_MOD}.litellm") as mock_litellm,
            patch(f"{_MOD}.LiteLLMAuthenticationError", err_cls),
        ):
            mock_litellm.acompletion = AsyncMock(side_effect=err_cls("bad key"))
            with pytest.raises(RuntimeError, match="Authentication failed"):
                await client.chat("sys", "user")
            assert mock_litellm.acompletion.call_count == 1


class TestLiteLLMClientContextManager:
    async def test_context_manager_noop(self) -> None:
        client = LiteLLMClient(model="test-model")
        async with client as c:
            assert c is client

    def test_implements_protocol(self) -> None:
        client = LiteLLMClient(model="test-model")
        assert isinstance(client, LLMClient)


class TestGetAvailableModels:
    def test_filters_to_supported_providers(self) -> None:
        mock_models = {
            "gemini": ["gemini-3.1-pro", "gemini-3.1-flash"],
            "anthropic": ["claude-sonnet-4-20250514"],
            "openai": ["gpt-4o"],
            "azure": ["gpt-4"],
            "cohere": ["command-r"],
        }
        with patch(f"{_MOD}.litellm") as mock_litellm:
            mock_litellm.models_by_provider = mock_models
            result = get_available_models()

        assert set(result.keys()) == {"gemini", "anthropic", "openai"}
        assert "gemini-3.1-pro" in result["gemini"]

    def test_returns_sorted_models(self) -> None:
        mock_models = {
            "gemini": ["gemini-3.1-pro", "gemini-2.5-flash", "gemini-3.1-flash"],
            "anthropic": [],
            "openai": [],
        }
        with patch(f"{_MOD}.litellm") as mock_litellm:
            mock_litellm.models_by_provider = mock_models
            result = get_available_models()

        assert result["gemini"] == sorted(mock_models["gemini"])


class TestValidateModelEnv:
    def test_delegates_to_litellm(self) -> None:
        with patch(f"{_MOD}.litellm") as mock_litellm:
            mock_litellm.validate_environment.return_value = {
                "keys_in_environment": True,
                "missing_keys": [],
            }
            result = validate_model_env("gemini/gemini-3.1-pro")

        mock_litellm.validate_environment.assert_called_once_with("gemini/gemini-3.1-pro")
        assert result["keys_in_environment"] is True
