"""Common contracts and behavior shared by LLM provider strategies."""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable

import httpx
from pydantic import BaseModel, ValidationError

from smart_search.llm.exceptions import (
    LLMError,
    LLMProviderError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
)

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Provider-neutral token counts when the provider supplies them."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Normalized result for ordinary text generation."""

    content: str
    provider: str
    model: str
    duration_ms: float
    usage: TokenUsage | None = None


@dataclass(frozen=True, slots=True)
class ProviderPayload:
    """Internal normalized payload returned by a provider implementation."""

    content: str
    usage: TokenUsage | None = None


@runtime_checkable
class LLMClient(Protocol):
    """Provider-independent interface for text and validated JSON generation."""

    async def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        """Generate ordinary text and return normalized response metadata."""

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ModelT],
    ) -> ModelT:
        """Generate JSON and validate it as the requested Pydantic model."""

    async def aclose(self) -> None:
        """Release resources owned by this client."""


class BaseLLMProvider(ABC):
    """Shared timeout, validation, logging, and error behavior for strategies."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        timeout_seconds: float,
        temperature: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._owns_http_client = http_client is None
        self._http_client = http_client

    async def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        return await self._request(system_prompt, user_prompt, json_schema=None)

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ModelT],
    ) -> ModelT:
        schema = response_model.model_json_schema()
        schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        structured_system_prompt = (
            f"{system_prompt.rstrip()}\n\n"
            "Return only one JSON object matching this JSON Schema. "
            "Do not wrap it in Markdown fences.\n"
            f"JSON Schema: {schema_text}"
        ).strip()
        response = await self._request(
            structured_system_prompt,
            user_prompt,
            json_schema=schema,
        )
        try:
            return response_model.model_validate_json(response.content)
        except ValidationError as exc:
            logger.warning(
                "llm_structured_validation_failed provider=%s model=%s "
                "duration_ms=%.2f",
                self.provider,
                self.model,
                response.duration_ms,
            )
            raise LLMResponseError(
                f"{self.provider} returned JSON that does not match the requested schema"
            ) from exc

    async def aclose(self) -> None:
        if self._owns_http_client and self._http_client is not None:
            await self._http_client.aclose()

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_seconds)
            )
        return self._http_client

    async def _request(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_schema: dict[str, Any] | None,
    ) -> LLMResponse:
        started_at = time.perf_counter()
        succeeded = False
        try:
            payload = await self._send(
                system_prompt,
                user_prompt,
                json_schema=json_schema,
            )
            if not payload.content or not payload.content.strip():
                raise LLMResponseError(f"{self.provider} returned an empty response")
            succeeded = True
            return LLMResponse(
                content=payload.content,
                provider=self.provider,
                model=self.model,
                duration_ms=(time.perf_counter() - started_at) * 1000,
                usage=payload.usage,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"{self.provider} request exceeded the configured timeout"
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {408, 425, 429} or status_code >= 500:
                raise LLMUnavailableError(
                    f"{self.provider} is temporarily unavailable (HTTP {status_code})"
                ) from exc
            raise LLMProviderError(
                f"{self.provider} rejected the request (HTTP {status_code})"
            ) from exc
        except httpx.RequestError as exc:
            raise LLMUnavailableError(f"{self.provider} could not be reached") from exc
        except LLMError:
            raise
        finally:
            logger.info(
                "llm_request provider=%s model=%s duration_ms=%.2f success=%s",
                self.provider,
                self.model,
                (time.perf_counter() - started_at) * 1000,
                succeeded,
            )

    @abstractmethod
    async def _send(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_schema: dict[str, Any] | None,
    ) -> ProviderPayload:
        """Send one provider-specific request and normalize its response."""

