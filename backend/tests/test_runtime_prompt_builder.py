"""Tests for app/runtime/prompt_builder.py (Sprint P6).

Constructs AgentDefinition instances directly in memory (no database
needed - the prompt builder only reads attributes already set), the
same way app.llm.models dataclasses are exercised directly in
tests/test_llm_gateway.py without any FastAPI/DB machinery.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.agent_definition import AgentDefinition
from app.models.enums import AgentStatus
from app.runtime.exceptions import AgentConfigurationError
from app.runtime.prompt_builder import build_generate_request


def _agent(**config_overrides: object) -> AgentDefinition:
    configuration_json: dict[str, object] = {
        "system_prompt": "You are a helpful test agent.",
        "model": "fake-v1",
    }
    configuration_json.update(config_overrides)
    return AgentDefinition(
        id=uuid.uuid4(),
        name="test-agent",
        role="Tester",
        status=AgentStatus.ACTIVE,
        configuration_json=configuration_json,
    )


def test_builds_request_from_required_config_only() -> None:
    request = build_generate_request(agent=_agent(), user_input="hello")

    assert request.model == "fake-v1"
    assert request.system_prompt == "You are a helpful test agent."
    assert len(request.messages) == 1
    assert request.messages[0].role == "user"
    assert request.messages[0].content == "hello"
    assert request.provider is None
    assert request.temperature is None
    assert request.max_tokens is None


def test_optional_provider_temperature_max_tokens_are_forwarded() -> None:
    agent = _agent(provider="fake", temperature=0.5, max_tokens=256)
    request = build_generate_request(agent=agent, user_input="hello")

    assert request.provider == "fake"
    assert request.temperature == 0.5
    assert request.max_tokens == 256


def test_context_is_appended_to_the_user_message() -> None:
    request = build_generate_request(
        agent=_agent(), user_input="summarize this", context={"topic": "widgets"}
    )

    content = request.messages[0].content
    assert content.startswith("summarize this")
    assert "widgets" in content


def test_no_context_leaves_user_message_unchanged() -> None:
    request = build_generate_request(agent=_agent(), user_input="hello", context=None)
    assert request.messages[0].content == "hello"


def test_empty_context_dict_leaves_user_message_unchanged() -> None:
    request = build_generate_request(agent=_agent(), user_input="hello", context={})
    assert request.messages[0].content == "hello"


def test_metadata_identifies_the_originating_agent() -> None:
    agent = _agent()
    request = build_generate_request(agent=agent, user_input="hello")

    assert request.metadata["agent_id"] == str(agent.id)
    assert request.metadata["agent_name"] == agent.name


def test_missing_system_prompt_raises_agent_configuration_error() -> None:
    agent = _agent()
    agent.configuration_json = {"model": "fake-v1"}

    with pytest.raises(AgentConfigurationError, match="system_prompt"):
        build_generate_request(agent=agent, user_input="hello")


def test_missing_model_raises_agent_configuration_error() -> None:
    agent = _agent()
    agent.configuration_json = {"system_prompt": "You are helpful."}

    with pytest.raises(AgentConfigurationError, match="model"):
        build_generate_request(agent=agent, user_input="hello")


def test_empty_configuration_json_reports_both_missing_keys() -> None:
    agent = _agent()
    agent.configuration_json = {}

    with pytest.raises(AgentConfigurationError) as excinfo:
        build_generate_request(agent=agent, user_input="hello")
    assert "system_prompt" in str(excinfo.value)
    assert "model" in str(excinfo.value)
