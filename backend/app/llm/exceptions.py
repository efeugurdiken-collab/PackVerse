"""LLM Gateway exception hierarchy (Sprint P5).

Provider adapters (app/llm/providers/) and the gateway (app/llm/gateway.py)
raise these; app/api/v1/llm.py is the only place that maps them to HTTP
status codes - the same "domain error, translated at the API boundary"
pattern used by app/services/exceptions.py for the rest of the app.

Every exception carries a class-level `retryable` flag so the gateway's
retry policy (app/llm/gateway.py) can decide whether to back off and try
again purely from the exception type, without a second hardcoded list of
"which errors are transient" living somewhere else and inevitably
drifting out of sync with this one.
"""
from __future__ import annotations

from typing import ClassVar


class LLMError(Exception):
    """Base class for all LLM Gateway errors.

    Messages on every subclass here are deliberately generic - never
    include the API key, full authorization header, or raw provider
    response body. See app/llm/providers/*.py for where secrets are kept
    out of exception messages at the source.
    """

    retryable: ClassVar[bool] = False


class LLMProviderNotConfigured(LLMError):
    """Raised lazily, only when a provider is actually selected (by an
    explicit request or the configured default) and turns out to be
    missing required credentials, or is not in LLM_ALLOWED_PROVIDERS -
    never eagerly at Settings-construction time. See
    app/llm/factory.py."""

    def __init__(self, provider: str, reason: str = "") -> None:
        message = f"LLM provider {provider!r} is not configured"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)
        self.provider = provider


class LLMAuthenticationError(LLMError):
    """The provider rejected our credentials (401/403). Never retryable -
    retrying with the same bad key just fails again."""

    def __init__(self, provider: str) -> None:
        super().__init__(f"Authentication with LLM provider {provider!r} failed")
        self.provider = provider


class LLMRateLimitError(LLMError):
    retryable: ClassVar[bool] = True

    def __init__(self, provider: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(f"LLM provider {provider!r} rate-limited this request")
        self.provider = provider
        self.retry_after_seconds = retry_after_seconds


class LLMTimeoutError(LLMError):
    retryable: ClassVar[bool] = True

    def __init__(self, provider: str) -> None:
        super().__init__(f"Request to LLM provider {provider!r} timed out")
        self.provider = provider


class LLMProviderUnavailable(LLMError):
    """Covers both a provider 5xx response and a transient network
    failure reaching the provider (connection refused/reset, DNS
    failure, etc.) - both are "try again later", not "this request is
    wrong"."""

    retryable: ClassVar[bool] = True

    def __init__(self, provider: str, reason: str = "") -> None:
        message = f"LLM provider {provider!r} is unavailable"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)
        self.provider = provider


class LLMInvalidRequest(LLMError):
    """The provider rejected the request itself (400/422) - a bad
    parameter, not a transient condition. Retrying unchanged would fail
    identically every time."""

    def __init__(self, provider: str, detail: str = "") -> None:
        message = f"LLM provider {provider!r} rejected the request as invalid"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.provider = provider


class LLMUnsupportedModel(LLMError):
    def __init__(self, provider: str, model: str) -> None:
        super().__init__(f"Model {model!r} is not supported by provider {provider!r}")
        self.provider = provider
        self.model = model


class LLMStructuredOutputError(LLMError):
    """The provider's response either wasn't valid JSON or didn't match
    the requested response_format schema. `raw_text` is kept as an
    attribute for internal/debug use only (e.g. server-side logging at
    debug level) - app/api/v1/llm.py must never include it in an API
    error response body."""

    def __init__(self, message: str, raw_text: str | None = None) -> None:
        super().__init__(message)
        self.raw_text = raw_text


class LLMEmbeddingNotSupported(LLMError):
    """Raised immediately (no HTTP call attempted) by a provider with no
    embeddings API at all - e.g. Anthropic (Sprint P10A). A permanent
    capability gap, not a transient condition - never retryable, and
    choosing a different provider is the only fix, not retrying the same
    request."""

    def __init__(self, provider: str) -> None:
        super().__init__(f"LLM provider {provider!r} does not support embeddings")
        self.provider = provider


class LLMResponseError(LLMError):
    """The provider returned a 2xx response but its shape didn't match
    what the adapter expected (missing fields, unexpected type, etc.) -
    a provider-side or adapter-side contract problem, not something a
    blind retry fixes."""

    def __init__(self, provider: str, detail: str = "") -> None:
        message = f"LLM provider {provider!r} returned an unexpected response"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.provider = provider
