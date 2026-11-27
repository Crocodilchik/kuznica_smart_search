"""Factory for constructing the configured LLM strategy."""

import httpx

from smart_search.llm.client import LLMClient
from smart_search.llm.config import LLMSettings
from smart_search.llm.exceptions import LLMConfigurationError
from smart_search.llm.providers.deepseek import DeepSeekProvider
from smart_search.llm.providers.mock import MockLLMProvider
from smart_search.llm.providers.ollama import OllamaProvider


class LLMFactory:
    """Create one provider from settings; never switch providers on failure."""

    @staticmethod
    def create(
        settings: LLMSettings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> LLMClient:
        config = settings or LLMSettings()
        common = {
            "timeout_seconds": config.timeout_seconds,
            "temperature": config.temperature,
            "http_client": http_client,
        }
        if config.provider == "deepseek":
            if not config.deepseek_api_key.strip():
                raise LLMConfigurationError(
                    "DEEPSEEK_API_KEY is required when LLM_PROVIDER=deepseek"
                )
            return DeepSeekProvider(
                api_key=config.deepseek_api_key,
                base_url=str(config.deepseek_base_url),
                model=config.deepseek_model,
                **common,
            )
        if config.provider == "ollama":
            return OllamaProvider(
                base_url=str(config.ollama_base_url),
                model=config.ollama_model,
                **common,
            )
        if config.provider == "mock":
            return MockLLMProvider()
        # LLMSettings validates the provider; retain a safe failure if that contract changes.
        raise LLMConfigurationError(f"Unsupported LLM provider: {config.provider}")
