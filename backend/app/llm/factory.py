"""Builds and caches the configured LLM provider registry (Sprint P5).

Mirrors app/storage/factory.py's pattern for the storage layer: business
logic (app/llm/gateway.py) depends only on app.llm.base.LLMProvider, and
this module is the only place concrete provider classes
(AnthropicProvider, OpenAICompatibleProvider, FakeProvider) are ever
imported and constructed.
"""
from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.exceptions import LLMProviderNotConfigured
from app.llm.gateway import LLMGateway
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.fake import FakeProvider
from app.llm.providers.openai_compatible import OpenAICompatibleProvider


def build_provider(name: str, settings: Settings) -> LLMProvider:
    """Constructs a single provider by name.

    Raises LLMProviderNotConfigured lazily - only when this specific
    provider is actually requested - if required credentials are
    missing, per the sprint spec: "missing credentials must fail only
    when that provider is selected". Settings itself never validates
    ANTHROPIC_API_KEY/OPENAI_API_KEY for presence at startup.
    """
    if name == "fake":
        return FakeProvider()

    if name == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMProviderNotConfigured("anthropic", "ANTHROPIC_API_KEY is not set")
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    if name == "openai":
        if not settings.openai_api_key:
            raise LLMProviderNotConfigured("openai", "OPENAI_API_KEY is not set")
        return OpenAICompatibleProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            organization=settings.openai_organization,
            project=settings.openai_project,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    raise LLMProviderNotConfigured(name, "unknown provider")


@lru_cache
def get_provider_registry() -> dict[str, LLMProvider]:
    """Cached for the process lifetime, same rationale as
    app.storage.factory.get_storage_backend: constructing a provider
    (an httpx client config, or the fake provider) is meant to happen
    once, not per-request.

    Only contains providers that are both in LLM_ALLOWED_PROVIDERS and
    successfully constructed - a configured-but-uncredentialed provider
    (e.g. "anthropic" allowed but ANTHROPIC_API_KEY unset) is simply
    absent from this dict; app/llm/gateway.py raises
    LLMProviderNotConfigured the moment something actually tries to use
    it, not before.
    """
    settings = get_settings()
    registry: dict[str, LLMProvider] = {}
    for name in settings.llm_allowed_providers_list:
        try:
            registry[name] = build_provider(name, settings)
        except LLMProviderNotConfigured:
            continue
    return registry


@lru_cache
def get_llm_gateway() -> LLMGateway:
    """The FastAPI dependency app/api/v1/llm.py resolves via
    Depends(get_llm_gateway) - same override pattern tests already use
    for Depends(get_storage_backend) in tests/conftest.py's `client`
    fixture: tests replace this with a gateway wrapping fake/mocked
    providers via app.dependency_overrides, never the real registry."""
    settings = get_settings()
    return LLMGateway(get_provider_registry(), settings)
