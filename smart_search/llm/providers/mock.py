"""Deterministic provider for tests and offline development."""

from __future__ import annotations

from typing import Any

from smart_search.llm.client import BaseLLMProvider, ProviderPayload
from smart_search.llm.exceptions import LLMError


class MockLLMProvider(BaseLLMProvider):
    """Returns a fixed response or raises a configured LLM error without network I/O."""

    def __init__(
        self,
        response_text: str = "Mock LLM response",
        *,
        error: LLMError | None = None,
        model: str = "mock-model",
    ) -> None:
        super().__init__(
            provider="mock",
            model=model,
            timeout_seconds=1.0,
            temperature=0.0,
        )
        self._response_text = response_text
        self._error = error
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _send(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_schema: dict[str, Any] | None,
    ) -> ProviderPayload:
        self.calls.append((system_prompt, user_prompt, json_schema))
        if self._error is not None:
            raise self._error
        return ProviderPayload(content=self._response_text)
