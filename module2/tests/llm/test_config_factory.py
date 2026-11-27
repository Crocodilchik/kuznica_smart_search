import pytest

from smart_search.llm import (
    DeepSeekProvider,
    LLMConfigurationError,
    LLMFactory,
    LLMSettings,
    MockLLMProvider,
    OllamaProvider,
)


def test_settings_read_public_environment_names(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "8")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.25")
    monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")

    settings = LLMSettings(_env_file=None)

    assert settings.provider == "ollama"
    assert settings.timeout_seconds == 8
    assert settings.temperature == 0.25
    assert settings.ollama_model == "local-test-model"


def test_factory_selects_ollama_without_api_key():
    settings = LLMSettings(_env_file=None, provider="ollama")

    assert isinstance(LLMFactory.create(settings), OllamaProvider)


def test_factory_selects_deepseek_when_configured():
    settings = LLMSettings(
        _env_file=None,
        provider="deepseek",
        deepseek_api_key="test-key",
    )

    assert isinstance(LLMFactory.create(settings), DeepSeekProvider)


def test_factory_requires_deepseek_key_and_does_not_fall_back():
    settings = LLMSettings(_env_file=None, provider="deepseek", deepseek_api_key="")

    with pytest.raises(LLMConfigurationError, match="DEEPSEEK_API_KEY"):
        LLMFactory.create(settings)


def test_factory_can_construct_mock_provider():
    settings = LLMSettings(_env_file=None, provider="mock")

    assert isinstance(LLMFactory.create(settings), MockLLMProvider)


def test_invalid_timeout_is_rejected():
    with pytest.raises(ValueError):
        LLMSettings(_env_file=None, timeout_seconds=0)
