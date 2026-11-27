"""DeepSeek Chat Completions adapter."""

from __future__ import annotations

from typing import Any

import httpx

from smart_search.llm.client import BaseLLMProvider, ProviderPayload, TokenUsage
from smart_search.llm.exceptions import LLMConfigurationError, LLMResponseError


class DeepSeekProvider(BaseLLMProvider):
    """Strategy that encapsulates the DeepSeek HTTP API format."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 5.0,
        temperature: float = 0.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise LLMConfigurationError("DeepSeek API key must not be empty")
        super().__init__(
            provider="deepseek",
            model=model,
            timeout_seconds=timeout_seconds,
            temperature=temperature,
            http_client=http_client,
        )
        self._api_key = api_key
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"

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
            "temperature": self.temperature,
            "stream": False,
        }
        if json_schema is not None:
            # DeepSeek's JSON mode guarantees JSON syntax; Pydantic validates the schema.
            body["response_format"] = {"type": "json_object"}

        response = await self._client.post(
            self._endpoint,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=body,
        )
        response.raise_for_status()
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("DeepSeek returned an unexpected response body") from exc
        if not isinstance(content, str):
            raise LLMResponseError("DeepSeek response content was not text")

        usage_data = data.get("usage") if isinstance(data, dict) else None
        usage = None
        if isinstance(usage_data, dict):
            usage = TokenUsage(
                input_tokens=_optional_int(usage_data.get("prompt_tokens")),
                output_tokens=_optional_int(usage_data.get("completion_tokens")),
                total_tokens=_optional_int(usage_data.get("total_tokens")),
            )
        return ProviderPayload(content=content, usage=usage)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
