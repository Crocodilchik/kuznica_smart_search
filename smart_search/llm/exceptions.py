"""Provider-neutral errors raised by the LLM client."""


class LLMError(RuntimeError):
    """Base class for errors that callers may handle uniformly."""


class LLMConfigurationError(LLMError):
    """The selected provider is not configured correctly."""


class LLMTimeoutError(LLMError):
    """A provider did not respond within the configured timeout."""


class LLMUnavailableError(LLMError):
    """A provider could not be reached or is temporarily unavailable."""


class LLMProviderError(LLMError):
    """A provider rejected the request or returned an unsuccessful status."""


class LLMResponseError(LLMError):
    """A provider returned an empty, malformed, or schema-invalid response."""
