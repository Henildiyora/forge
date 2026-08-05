"""Anthropic Messages API client used by the Ops Agent.

Carried over from the previous ``core/llm.py`` ``AnthropicProvider``, narrowed
to one backend and reworked to use tool calling so the model must return a
payload matching a JSON schema rather than prose we then have to parse.
"""

from __future__ import annotations

from typing import Any

import httpx

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(RuntimeError):
    """Raised when the model call fails or returns an unusable response."""


class AnthropicClient:
    """Thin client that forces a single structured tool call."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int = 2048,
        timeout_seconds: float = 60.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    def structured_call(
        self,
        *,
        system: str,
        user: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Call the model and return the arguments of its forced tool use."""

        try:
            response = self._client.post(
                ANTHROPIC_MESSAGES_URL,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "system": system,
                    "tools": [
                        {
                            "name": tool_name,
                            "description": tool_description,
                            "input_schema": input_schema,
                        }
                    ],
                    "tool_choice": {"type": "tool", "name": tool_name},
                    "messages": [{"role": "user", "content": user}],
                },
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"anthropic request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMError(
                f"anthropic returned {response.status_code}: {response.text[:400]}"
            )

        payload = response.json()
        for block in payload.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                arguments = block.get("input")
                if isinstance(arguments, dict):
                    return arguments
        raise LLMError("anthropic response contained no tool_use block")

    def close(self) -> None:
        """Release the connection pool when we own it."""

        if self._owns_client:
            self._client.close()
