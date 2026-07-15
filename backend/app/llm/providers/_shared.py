"""Shared HTTP-error-mapping helpers for the httpx-based provider
adapters (anthropic.py, openai_compatible.py).

Not part of the public LLMProvider interface - a private implementation
detail shared between the two adapters so the status-code -> LLMError
mapping table (and retry-after parsing) isn't duplicated twice with a
risk of silently drifting out of sync.
"""
from __future__ import annotations

import httpx

from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMError,
    LLMInvalidRequest,
    LLMProviderUnavailable,
    LLMRateLimitError,
    LLMResponseError,
    LLMUnsupportedModel,
)


def parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def extract_error_message(response: httpx.Response) -> str:
    """Best-effort human-readable message from an OpenAI- or
    Anthropic-shaped error body: `{"error": {"message": "..."}}` (both
    providers) or `{"error": "..."}` (some OpenAI-compatible gateways).
    Truncated - this only ever ends up inside an LLMError message, never
    logged or returned verbatim to a client beyond that."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message", ""))[:200]
        if isinstance(error, str):
            return error[:200]
    return str(body)[:200]


def map_http_error(
    provider: str,
    model: str,
    response: httpx.Response,
    *,
    not_found_is_unsupported_model: bool = True,
) -> LLMError:
    """Maps an HTTP error response to a normalized LLMError. Never
    includes an API key or authorization header - this function never
    receives them, only the response object and public request
    metadata."""
    status = response.status_code
    message = extract_error_message(response)

    if status in (401, 403):
        return LLMAuthenticationError(provider)
    if status == 429:
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        return LLMRateLimitError(provider, retry_after_seconds=retry_after)
    if status == 404 and not_found_is_unsupported_model:
        return LLMUnsupportedModel(provider, model)
    if status in (400, 413, 422):
        return LLMInvalidRequest(provider, message)
    if status >= 500:
        return LLMProviderUnavailable(provider, f"HTTP {status}")
    return LLMResponseError(provider, f"HTTP {status}: {message}")
