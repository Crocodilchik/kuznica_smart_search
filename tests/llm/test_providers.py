import json

import httpx
import pytest
from pydantic import BaseModel

from smart_search.llm import (
    DeepSeekProvider,
    LLMProviderError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    MockLLMProvider,
    OllamaProvider,
)


class ExtractedQuery(BaseModel):
    intent: str
    devices: int


@pytest.mark.asyncio
async def test_deepseek_text_generation_normalizes_response_and_usage():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers.get("Authorization")
        observed["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hello"}}],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = DeepSeekProvider(
            api_key="unit-test-key",
            base_url="https://api.deepseek.test",
            model="test-model",
            http_client=http_client,
        )
        result = await provider.generate("system", "user")

    assert result.content == "hello"
    assert result.provider == "deepseek"
    assert result.model == "test-model"
    assert result.duration_ms >= 0
    assert result.usage is not None
    assert result.usage.total_tokens == 6
    assert observed["url"] == "https://api.deepseek.test/chat/completions"
    assert observed["authorization"] == "Bearer unit-test-key"
    assert observed["body"]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]


@pytest.mark.asyncio
async def test_deepseek_structured_generation_uses_json_mode_and_validates():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"intent":"search","devices":12}'}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = DeepSeekProvider(
            api_key="unit-test-key",
            base_url="https://api.deepseek.test",
            model="test-model",
            http_client=http_client,
        )
        result = await provider.complete_json("extract", "find antivirus", ExtractedQuery)

    assert result == ExtractedQuery(intent="search", devices=12)
    assert observed["body"]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in observed["body"]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_ollama_sends_schema_temperature_and_system_prompt():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": {"content": '{"intent":"search","devices":12}'},
                "prompt_eval_count": 5,
                "eval_count": 3,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = OllamaProvider(
            base_url="http://ollama.test:11434",
            model="qwen3:test",
            temperature=0.2,
            http_client=http_client,
        )
        result = await provider.complete_json("extract", "find antivirus", ExtractedQuery)

    assert result.intent == "search"
    assert result.devices == 12
    assert observed["url"] == "http://ollama.test:11434/api/chat"
    assert observed["body"]["stream"] is False
    assert observed["body"]["options"]["temperature"] == 0.2
    assert observed["body"]["format"] == ExtractedQuery.model_json_schema()
    assert observed["body"]["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_mock_provider_validates_structured_response_without_network():
    provider = MockLLMProvider('{"intent":"search","devices":4}')

    result = await provider.complete_json("system", "user", ExtractedQuery)

    assert result == ExtractedQuery(intent="search", devices=4)
    assert len(provider.calls) == 1
    assert provider.calls[0][2] == ExtractedQuery.model_json_schema()
    await provider.aclose()


@pytest.mark.asyncio
async def test_invalid_json_or_schema_raises_unified_response_error():
    for body in ("not json", '{"intent":"search","devices":"many"}'):
        provider = MockLLMProvider(body)
        with pytest.raises(LLMResponseError):
            await provider.complete_json("system", "user", ExtractedQuery)


@pytest.mark.asyncio
async def test_empty_provider_response_raises_unified_response_error():
    provider = MockLLMProvider("  ")

    with pytest.raises(LLMResponseError, match="empty response"):
        await provider.generate("system", "user")


class _FailingTransport(httpx.AsyncBaseTransport):
    def __init__(self, error: Exception):
        self.error = error

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise self.error


@pytest.mark.asyncio
async def test_timeout_and_unavailable_errors_are_unified():
    cases = [
        (httpx.ReadTimeout("slow"), LLMTimeoutError),
        (httpx.ConnectError("offline"), LLMUnavailableError),
    ]
    for transport_error, expected_error in cases:
        http_client = httpx.AsyncClient(transport=_FailingTransport(transport_error))
        provider = OllamaProvider(
            base_url="http://ollama.test",
            model="test-model",
            http_client=http_client,
        )
        with pytest.raises(expected_error):
            await provider.generate("system", "user")
        await http_client.aclose()


@pytest.mark.asyncio
async def test_provider_http_errors_are_unified_without_response_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="sensitive provider response body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = DeepSeekProvider(
            api_key="unit-test-key",
            base_url="https://api.deepseek.test",
            model="test-model",
            http_client=http_client,
        )
        with pytest.raises(LLMProviderError, match="HTTP 401") as caught:
            await provider.generate("system", "user")

    assert "sensitive provider response body" not in str(caught.value)


@pytest.mark.asyncio
async def test_malformed_provider_http_json_raises_response_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = DeepSeekProvider(
            api_key="unit-test-key",
            base_url="https://api.deepseek.test",
            model="test-model",
            http_client=http_client,
        )
        with pytest.raises(LLMResponseError, match="unexpected response body"):
            await provider.generate("system", "user")


@pytest.mark.asyncio
async def test_temporary_provider_http_status_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = OllamaProvider(
            base_url="http://ollama.test",
            model="test-model",
            http_client=http_client,
        )
        with pytest.raises(LLMUnavailableError, match="HTTP 503"):
            await provider.generate("system", "user")


@pytest.mark.asyncio
async def test_structured_schema_failure_is_logged_without_prompt_content(caplog):
    provider = MockLLMProvider('{"intent":"search","devices":"bad"}')

    with pytest.raises(LLMResponseError):
        await provider.complete_json("secret system prompt", "private user query", ExtractedQuery)

    assert "provider=mock" in caplog.text
    assert "model=mock-model" in caplog.text
    assert "duration_ms=" in caplog.text
    assert "private user query" not in caplog.text
    assert "secret system prompt" not in caplog.text


@pytest.mark.asyncio
async def test_mock_can_raise_unified_failure():
    provider = MockLLMProvider(error=LLMUnavailableError("offline"))

    with pytest.raises(LLMUnavailableError, match="offline"):
        await provider.generate("system", "user")
