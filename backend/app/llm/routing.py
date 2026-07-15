"""Provider and model-alias routing (Sprint P5).

Provider resolution order:
1. explicit `provider` on the request
2. settings.llm_default_provider
3. LLMProviderNotConfigured - fail clearly if neither resolves

Model aliases (fast/balanced/quality, or any other name) resolve
entirely through settings.llm_model_aliases_map - never a hardcoded
model name in this module.
"""
from __future__ import annotations

from app.core.config import Settings
from app.llm.exceptions import LLMProviderNotConfigured


def resolve_provider_name(explicit_provider: str | None, settings: Settings) -> str:
    allowed = set(settings.llm_allowed_providers_list)

    if explicit_provider is not None:
        if explicit_provider not in allowed:
            raise LLMProviderNotConfigured(explicit_provider, "not in LLM_ALLOWED_PROVIDERS")
        return explicit_provider

    if settings.llm_default_provider is not None:
        if settings.llm_default_provider not in allowed:
            # Settings.validate_llm_default_provider_is_allowed already
            # guarantees this can't happen for a real Settings instance -
            # this branch is a defensive second check, not the primary
            # enforcement point.
            raise LLMProviderNotConfigured(
                settings.llm_default_provider, "not in LLM_ALLOWED_PROVIDERS"
            )
        return settings.llm_default_provider

    raise LLMProviderNotConfigured(
        "none", "no explicit provider in the request and no LLM_DEFAULT_PROVIDER configured"
    )


def resolve_model(provider: str, requested_model: str, settings: Settings) -> str:
    """Resolves an alias (e.g. "fast") to a real model name for the
    given provider. A model name that isn't a known alias is returned
    unchanged - callers may always pass a real model name directly.

    `model` is a required field on the request (see app/schemas/llm.py's
    GenerateRequest) - there is no "omit it and get some default"
    behavior here. ANTHROPIC_DEFAULT_MODEL/OPENAI_DEFAULT_MODEL/
    LLM_DEFAULT_MODEL exist purely as informational defaults surfaced by
    GET /api/v1/llm/providers and /models, not as an implicit fallback
    inside routing itself."""
    aliases = settings.llm_model_aliases_map.get(provider, {})
    return aliases.get(requested_model, requested_model)


def default_model_for(provider: str, settings: Settings) -> str | None:
    """Informational only - used by GET /providers and /models to show
    a suggested default, never consulted by resolve_model/generate()."""
    provider_defaults = {
        "anthropic": settings.anthropic_default_model,
        "openai": settings.openai_default_model,
    }
    return provider_defaults.get(provider) or settings.llm_default_model
