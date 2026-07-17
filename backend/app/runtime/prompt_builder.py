"""Reusable prompt construction (Sprint P6): the one place an
AgentDefinition's configuration_json plus a caller's user_input/context
get turned into an LLM Gateway request. No other module builds a
GenerateRequest from an AgentDefinition - see the sprint's "avoid
duplicated prompt construction logic".

Convention for AgentDefinition.configuration_json (this sprint does not
add an AgentDefinition CRUD API - definitions are still seeded directly,
per app/schemas/agent_definition.py's docstring - so this convention is
enforced only here, at load time):
    system_prompt: str   (required)
    model: str            (required)
    provider: str | None  (optional - falls back to LLM_DEFAULT_PROVIDER)
    temperature: float | None
    max_tokens: int | None
"""
from __future__ import annotations

import json

from app.models.agent_definition import AgentDefinition
from app.runtime.exceptions import AgentConfigurationError
from app.schemas.llm import GenerateRequest, MessageIn

_REQUIRED_CONFIG_KEYS = ("system_prompt", "model")


def _require_config(agent: AgentDefinition) -> dict[str, object]:
    config = agent.configuration_json
    missing = [key for key in _REQUIRED_CONFIG_KEYS if not config.get(key)]
    if missing:
        raise AgentConfigurationError(
            agent.id, f"configuration_json missing required key(s): {', '.join(missing)}"
        )
    return config


def _render_user_message(user_input: str, context: dict[str, object] | None) -> str:
    if not context:
        return user_input
    rendered_context = json.dumps(context, indent=2, sort_keys=True, default=str)
    return f"{user_input}\n\n---\nContext:\n{rendered_context}"


def build_generate_request(
    *,
    agent: AgentDefinition,
    user_input: str,
    context: dict[str, object] | None = None,
) -> GenerateRequest:
    """Input: an AgentDefinition, the caller's user_input, and optional
    context. Output: a ready-to-use app.schemas.llm.GenerateRequest -
    literally "an LLM Gateway request", the exact type
    app.services.llm_service.generate_and_persist already accepts, so
    app/runtime/executor.py never has to build one by hand."""
    config = _require_config(agent)

    provider = config.get("provider")
    temperature = config.get("temperature")
    max_tokens = config.get("max_tokens")

    return GenerateRequest(
        provider=provider if isinstance(provider, str) else None,
        model=str(config["model"]),
        system_prompt=str(config["system_prompt"]),
        messages=[MessageIn(role="user", content=_render_user_message(user_input, context))],
        temperature=float(temperature) if isinstance(temperature, (int, float)) else None,
        max_tokens=int(max_tokens) if isinstance(max_tokens, int) else None,
        metadata={"agent_id": str(agent.id), "agent_name": agent.name},
    )
