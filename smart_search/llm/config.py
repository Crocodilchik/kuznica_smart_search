"""Validated environment configuration for provider selection."""

from typing import Literal

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["deepseek", "ollama", "mock"]


class LLMSettings(BaseSettings):
    """Settings use the public environment variable names from `.env.example`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    provider: ProviderName = Field(default="deepseek", validation_alias="LLM_PROVIDER")
    timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        validation_alias="LLM_TIMEOUT_SECONDS",
    )
    temperature: float = Field(
        default=0.0,
        ge=0,
        le=2,
        validation_alias="LLM_TEMPERATURE",
    )

    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")
    deepseek_base_url: AnyHttpUrl = Field(
        default="https://api.deepseek.com",
        validation_alias="DEEPSEEK_BASE_URL",
    )
    deepseek_model: str = Field(
        default="deepseek-chat",
        min_length=1,
        validation_alias="DEEPSEEK_MODEL",
    )

    ollama_base_url: AnyHttpUrl = Field(
        default="http://localhost:11434",
        validation_alias="OLLAMA_BASE_URL",
    )
    ollama_model: str = Field(
        default="qwen3:8b",
        min_length=1,
        validation_alias="OLLAMA_MODEL",
    )
