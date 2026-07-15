"""Tests for app.llm.gateway.LLMGateway: provider/model routing, retry
policy, cost estimation, and health aggregation.

Uses FakeProvider exclusively - no network, no HTTP mocking needed here.
Adapter-level HTTP request/response/error mapping is covered separately
in tests/test_llm_anthropic_adapter.py and tests/test_llm_openai_adapter.py.

Every test builds its own Settings instance with an explicit
jwt_secret_key so the dev-only auto-generate-and-persist-to-.env branch
in app.core.config.Settings.resolve_jwt_secret_key is never reached -
these tests must never write to a real .env file regardless of the
working directory pytest happens to run from.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.llm.exceptions import (
    LLMInvalidRequest,
    LLMProviderNotConfigured,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.llm.gateway import LLMGateway
from app.llm.models import LLMRequest, Message, ResponseFormat
from app.llm.providers.fake import FakeProvider


def _settings(**overrides: object) -> Settings:
    return Settings(jwt_secret_key="x" * 32, **overrides)


def _request(
    *,
    provider: str | None = None,
    model: str = "m1",
    response_format: ResponseFormat | None = None,
) -> LLMRequest:
    return LLMRequest(
        request_id=str(uuid.uuid4()),
        model=model,
        messages=(Message(role="user", content="hi"),),
        provider=provider,
        response_format=response_format,
    )


# --- Routing ----------------------------------------------------------


async def test_explicit_provider_routing() -> None:
    settings = _settings(llm_allowed_providers="fake")
    gateway = LLMGateway({"fake": FakeProvider()}, settings)

    response = await gateway.generate(_request(provider="fake"))

    assert response.provider == "fake"


async def test_default_provider_routing() -> None:
    settings = _settings(llm_allowed_providers="fake", llm_default_provider="fake")
    gateway = LLMGateway({"fake": FakeProvider()}, settings)

    response = await gateway.generate(_request(provider=None))

    assert response.provider == "fake"


async def test_missing_provider_raises_when_nothing_configured() -> None:
    settings = _settings(llm_allowed_providers="fake")  # no LLM_DEFAULT_PROVIDER
    gateway = LLMGateway({"fake": FakeProvider()}, settings)

    with pytest.raises(LLMProviderNotConfigured):
        await gateway.generate(_request(provider=None))


async def test_unsupported_provider_raises() -> None:
    settings = _settings(llm_allowed_providers="fake")
    gateway = LLMGateway({"fake": FakeProvider()}, settings)

    with pytest.raises(LLMProviderNotConfigured):
        await gateway.generate(_request(provider="anthropic"))


async def test_alias_resolution() -> None:
    settings = _settings(
        llm_allowed_providers="fake",
        llm_model_aliases='{"fake": {"fast": "fake-fast-model"}}',
    )
    gateway = LLMGateway({"fake": FakeProvider()}, settings)

    response = await gateway.generate(_request(provider="fake", model="fast"))

    assert response.model == "fake-fast-model"


async def test_model_that_is_not_a_known_alias_passes_through_unchanged() -> None:
    settings = _settings(llm_allowed_providers="fake")
    gateway = LLMGateway({"fake": FakeProvider()}, settings)

    response = await gateway.generate(_request(provider="fake", model="literal-model-name"))

    assert response.model == "literal-model-name"


# --- Retry policy -------------------------------------------------------


async def test_timeout_is_retried_and_eventually_succeeds() -> None:
    calls = {"n": 0}

    class FlakyProvider(FakeProvider):
        async def generate(self, request: LLMRequest):
            calls["n"] += 1
            if calls["n"] < 2:
                raise LLMTimeoutError("fake")
            return await super().generate(request)

    settings = _settings(llm_allowed_providers="fake", llm_max_retries=2)
    gateway = LLMGateway({"fake": FlakyProvider()}, settings, retry_base_delay_seconds=0.0)

    response = await gateway.generate(_request(provider="fake"))

    assert calls["n"] == 2
    assert response.content


async def test_retryable_error_exhausts_retries_and_raises() -> None:
    class AlwaysRateLimited(FakeProvider):
        async def generate(self, request: LLMRequest):
            raise LLMRateLimitError("fake")

    settings = _settings(llm_allowed_providers="fake", llm_max_retries=2)
    gateway = LLMGateway({"fake": AlwaysRateLimited()}, settings, retry_base_delay_seconds=0.0)

    with pytest.raises(LLMRateLimitError):
        await gateway.generate(_request(provider="fake"))


async def test_non_retryable_error_does_not_retry() -> None:
    calls = {"n": 0}

    class AlwaysInvalid(FakeProvider):
        async def generate(self, request: LLMRequest):
            calls["n"] += 1
            raise LLMInvalidRequest("fake")

    settings = _settings(llm_allowed_providers="fake", llm_max_retries=3)
    gateway = LLMGateway({"fake": AlwaysInvalid()}, settings, retry_base_delay_seconds=0.0)

    with pytest.raises(LLMInvalidRequest):
        await gateway.generate(_request(provider="fake"))

    assert calls["n"] == 1  # never retried


# --- Normalized response / cost ------------------------------------------


async def test_generate_returns_normalized_response() -> None:
    settings = _settings(llm_allowed_providers="fake")
    gateway = LLMGateway({"fake": FakeProvider()}, settings)

    response = await gateway.generate(_request(provider="fake"))

    assert response.request_id
    assert response.provider == "fake"
    assert response.finish_reason == "stop"
    assert response.usage.total_tokens == (
        response.usage.input_tokens + response.usage.output_tokens
    )


async def test_cost_estimation_uses_configured_pricing() -> None:
    settings = _settings(
        llm_allowed_providers="fake",
        llm_pricing_json='{"fake:m1": {"input_per_1k": "1.00", "output_per_1k": "2.00"}}',
    )
    gateway = LLMGateway({"fake": FakeProvider()}, settings)

    response = await gateway.generate(_request(provider="fake", model="m1"))

    assert response.estimated_cost_usd is not None
    assert response.estimated_cost_usd > Decimal("0")


async def test_unknown_pricing_returns_none_not_fabricated() -> None:
    settings = _settings(llm_allowed_providers="fake")  # no LLM_PRICING_JSON entry
    gateway = LLMGateway({"fake": FakeProvider()}, settings)

    response = await gateway.generate(_request(provider="fake", model="m1"))

    assert response.estimated_cost_usd is None


# --- Health + streaming -----------------------------------------------


async def test_health_check_never_raises_even_if_a_provider_misbehaves() -> None:
    class Explodes(FakeProvider):
        async def health_check(self):
            raise RuntimeError("boom")

    settings = _settings(llm_allowed_providers="fake")
    gateway = LLMGateway({"fake": Explodes()}, settings)

    results = await gateway.health_check()

    assert results[0].status == "unavailable"


async def test_health_check_reports_not_configured_for_allowed_but_uncredentialed_provider() -> (
    None
):
    settings = _settings(llm_allowed_providers="fake,anthropic")
    gateway = LLMGateway({"fake": FakeProvider()}, settings)  # anthropic absent - no API key

    results = await gateway.health_check()

    by_provider = {r.provider: r for r in results}
    assert by_provider["anthropic"].status == "not_configured"


async def test_stream_yields_chunks_and_a_final_finish_reason() -> None:
    settings = _settings(llm_allowed_providers="fake")
    gateway = LLMGateway({"fake": FakeProvider()}, settings)

    chunks = [chunk async for chunk in gateway.stream(_request(provider="fake"))]

    assert chunks
    assert chunks[-1].finish_reason == "stop"
