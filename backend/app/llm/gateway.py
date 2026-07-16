"""LLM Gateway (Sprint P5): the single entry point business logic uses to
call any configured provider.

Combines provider/model routing (app/llm/routing.py), a retry policy for
transient failures, structured-output JSON Schema validation, and cost
estimation (app/llm/pricing.py) into one call. app/services/llm_service.py
wraps this with database persistence; this module itself has no FastAPI
or SQLAlchemy imports.
"""
from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace

from jsonschema import ValidationError as JSONSchemaValidationError
from jsonschema import validate as jsonschema_validate

from app.core.config import Settings
from app.llm.base import LLMProvider
from app.llm.exceptions import LLMError, LLMProviderNotConfigured, LLMStructuredOutputError
from app.llm.models import LLMRequest, LLMResponse, ProviderHealth, StreamChunk
from app.llm.pricing import estimate_cost_usd
from app.llm.routing import resolve_model, resolve_provider_name

_BASE_RETRY_DELAY_SECONDS = 0.5


def _validate_structured_output(content: str, json_schema: Mapping[str, object]) -> None:
    """Raises LLMStructuredOutputError if `content` isn't valid JSON, or
    doesn't conform to `json_schema`. Runs regardless of whether the
    provider claims native JSON-mode support - the gateway is the single
    source of truth for this guarantee. `raw_text` is attached to the
    exception for internal/debug use only; app/api/v1/llm.py never
    includes it in an API error response."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMStructuredOutputError(
            "Provider response was not valid JSON", raw_text=content
        ) from exc

    try:
        jsonschema_validate(instance=parsed, schema=dict(json_schema))
    except JSONSchemaValidationError as exc:
        raise LLMStructuredOutputError(
            f"Response did not match the requested schema: {exc.message}", raw_text=content
        ) from exc


class LLMGateway:
    def __init__(
        self,
        providers: Mapping[str, LLMProvider],
        settings: Settings,
        *,
        retry_base_delay_seconds: float = _BASE_RETRY_DELAY_SECONDS,
    ) -> None:
        self._providers = dict(providers)
        self._settings = settings
        self._retry_base_delay_seconds = retry_base_delay_seconds

    def configured_providers(self) -> frozenset[str]:
        """Public accessor for app/services/llm_service.py's provider-
        listing endpoint support - callers outside this module should
        never reach into the private `_providers` mapping directly."""
        return frozenset(self._providers)

    def _get_provider(self, name: str) -> LLMProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise LLMProviderNotConfigured(name, "not configured in this gateway instance")
        return provider

    async def generate(self, request: LLMRequest) -> LLMResponse:
        provider_name = resolve_provider_name(request.provider, self._settings)
        model = resolve_model(provider_name, request.model, self._settings)
        provider = self._get_provider(provider_name)
        resolved_request = replace(request, provider=provider_name, model=model)

        response = await self._call_with_retry(provider, resolved_request)

        if resolved_request.response_format is not None:
            _validate_structured_output(
                response.content, resolved_request.response_format.json_schema
            )

        cost = estimate_cost_usd(
            self._settings,
            provider=provider_name,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        return replace(response, estimated_cost_usd=cost)

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        """No retry policy here - see the module docstring; a partially
        streamed response can't be safely retried without a well-defined
        client-side replay strategy, which is out of scope for the
        gateway's typed-but-unexposed streaming interface this sprint."""
        provider_name = resolve_provider_name(request.provider, self._settings)
        model = resolve_model(provider_name, request.model, self._settings)
        provider = self._get_provider(provider_name)
        resolved_request = replace(request, provider=provider_name, model=model)

        async for chunk in provider.stream(resolved_request):
            yield chunk

    async def health_check(self, provider_name: str | None = None) -> list[ProviderHealth]:
        """Never raises - a provider whose health_check() itself
        misbehaves is reported as unavailable rather than taking down
        the whole aggregate result, matching "A provider health failure
        must not crash the whole API".

        Iterates settings.llm_allowed_providers_list, not just
        self._providers: an allowed provider that simply lacks
        credentials (so it was never constructed into the registry - see
        app/llm/factory.py.get_provider_registry) must still show up as
        `not_configured`, not silently vanish from the report."""
        if provider_name is not None:
            names = [provider_name]
        else:
            names = list(self._settings.llm_allowed_providers_list)
        results: list[ProviderHealth] = []
        for name in names:
            provider = self._providers.get(name)
            if provider is None:
                results.append(ProviderHealth(provider=name, status="not_configured"))
                continue
            try:
                results.append(await provider.health_check())
            except Exception as exc:  # broad on purpose - see docstring above
                results.append(
                    ProviderHealth(provider=name, status="unavailable", detail=str(exc))
                )
        return results

    async def _call_with_retry(self, provider: LLMProvider, request: LLMRequest) -> LLMResponse:
        attempt = 0
        while True:
            try:
                return await provider.generate(request)
            except LLMError as exc:
                if not exc.retryable or attempt >= self._settings.llm_max_retries:
                    raise
                delay = self._retry_base_delay_seconds * (2**attempt) + random.uniform(
                    0, self._retry_base_delay_seconds
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                attempt += 1
