"""Local Ollama `/api/chat` adapter."""

from __future__ import annotations

from typing import Any

import httpx

from smart_search.llm.client import BaseLLMProvider, ProviderPayload, TokenUsage
from smart_search.llm.exceptions import LLMResponseError


class OllamaProvider(BaseLLMProvider):
    """Strategy that encapsulates Ollama's local chat API format."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 5.0,
        temperature: float = 0.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            provider="ollama",
            model=model,
            timeout_seconds=timeout_seconds,
            temperature=temperature,
            http_client=http_client,
        )
        self._endpoint = f"{base_url.rstrip('/')}/api/chat"

    async def _send(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_schema: dict[str, Any] | None,
    ) -> ProviderPayload:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        if json_schema is not None:
            body["format"] = json_schema

        response = await self._client.post(self._endpoint, json=body)
        response.raise_for_status()
        try:
            data = response.json()
            content = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMResponseError("Ollama returned an unexpected response body") from exc
        if not isinstance(content, str):
            raise LLMResponseError("Ollama response content was not text")

        usage = None
        if isinstance(data, dict):
            input_tokens = _optional_int(data.get("prompt_eval_count"))
            output_tokens = _optional_int(data.get("eval_count"))
            if input_tokens is not None or output_tokens is not None:
                usage = TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=(
                        input_tokens + output_tokens
                        if input_tokens is not None and output_tokens is not None
                        else None
                    ),
                )
        return ProviderPayload(content=content, usage=usage)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
