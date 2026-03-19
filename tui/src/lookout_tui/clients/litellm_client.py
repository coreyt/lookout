"""LiteLLM-based LLM client — routes to 100+ providers."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import litellm
from litellm.exceptions import APIConnectionError as LiteLLMAPIConnectionError
from litellm.exceptions import AuthenticationError as LiteLLMAuthenticationError
from litellm.exceptions import RateLimitError as LiteLLMRateLimitError
from litellm.exceptions import Timeout as LiteLLMTimeout

from lookout_tui.clients.protocol import LLMResponse

logger = logging.getLogger("lookout_tui.clients.litellm")

SUPPORTED_PROVIDERS = ("gemini", "anthropic", "openai")


def _airlock_router_config() -> dict[str, Any]:
    """Return LiteLLM kwargs for Airlock when AIRLOCK_* env vars are set."""
    host = os.environ.get("AIRLOCK_HOST", "").strip()
    port = os.environ.get("AIRLOCK_PORT", "").strip()
    if not host and not port:
        return {}
    if not host:
        host = "localhost"
    if not port:
        port = "4000"
    if host == "0.0.0.0":
        host = "127.0.0.1"

    config: dict[str, Any] = {
        "api_base": f"http://{host}:{port}/v1",
    }
    api_key = os.environ.get("AIRLOCK_MASTER_KEY", "").strip()
    if api_key:
        config["api_key"] = api_key

    airlock_client = os.environ.get("AIRLOCK_CLIENT", "").strip()
    if airlock_client:
        config["extra_headers"] = {
            "X-Airlock-Client": airlock_client,
        }
    return config


class LiteLLMClient:
    """Async LLM client using litellm for multi-provider support.

    Usage:
        async with LiteLLMClient(model="gemini/gemini-3.1-pro") as client:
            response = await client.chat("You are helpful.", "Hello!")
    """

    def __init__(
        self,
        model: str = "gemini/gemini-3.1-pro",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries

    async def __aenter__(self) -> LiteLLMClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        pass

    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMResponse:
        """Send a chat completion request via litellm.

        Args:
            system_prompt: System message content.
            user_prompt: User message content.

        Returns:
            LLMResponse with extracted content, model, and usage.

        Raises:
            RuntimeError: On authentication failure or retry exhaustion.
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        for attempt in range(self.max_retries):
            try:
                response = await litellm.acompletion(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    timeout=self.timeout,
                    num_retries=0,
                    **_airlock_router_config(),
                )
                return self._parse_response(response)
            except LiteLLMAuthenticationError as e:
                raise RuntimeError(f"Authentication failed for model {self.model}: {e}") from e
            except (LiteLLMRateLimitError, LiteLLMAPIConnectionError, LiteLLMTimeout) as e:
                if attempt < self.max_retries - 1:
                    wait = 2**attempt
                    logger.warning(
                        "%s: %s, retrying in %ds (attempt %d/%d)",
                        type(e).__name__,
                        e,
                        wait,
                        attempt + 1,
                        self.max_retries,
                    )
                    await asyncio.sleep(wait)
                else:
                    raise RuntimeError(
                        f"LLM call failed after {self.max_retries} attempts: {e}"
                    ) from e

        raise RuntimeError(f"LLM call failed after {self.max_retries} attempts")  # pragma: no cover

    @staticmethod
    def _parse_response(response: Any) -> LLMResponse:
        """Extract content, model, and usage from litellm response."""
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError) as e:
            raise RuntimeError(f"Unexpected response format: {response}") from e

        model = getattr(response, "model", "") or ""
        usage_obj = getattr(response, "usage", None)
        usage = {}
        if usage_obj:
            usage = {
                "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
                "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
                "total_tokens": getattr(usage_obj, "total_tokens", 0),
            }
        return LLMResponse(content=content, model=model, usage=usage)


def get_available_models() -> dict[str, list[str]]:
    """Return available models filtered to supported providers."""
    result: dict[str, list[str]] = {}
    for provider in SUPPORTED_PROVIDERS:
        models = litellm.models_by_provider.get(provider, [])
        result[provider] = sorted(models)
    return result


def validate_model_env(model: str) -> dict[str, Any]:
    """Check if environment variables are set for a model."""
    result: dict[str, Any] = litellm.validate_environment(model)
    return result
