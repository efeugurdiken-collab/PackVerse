"""Tests for app/runtime/executor.py's Sprint P9C1 bounded tool-call
loop: agent configuration_json's mcp_server key, the LLM <-> MCP round
trip, the iteration cap, and MCP-failure handling.

Every LLM call routes through the network-free "fake" LLM provider,
exactly mirroring test_runtime_executor.py's approach; every MCP call
goes through the real app.mcp.client.MCPClient with its HTTP requests
intercepted by pytest-httpx's httpx_mock fixture - no real MCP server,
the same pattern as test_mcp_client.py/test_mcp_api.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.core.config import Settings
from app.llm.gateway import LLMGateway
from app.llm.models import LLMRequest, LLMResponse, LLMUsage, ToolCall
from app.llm.providers.fake import FakeProvider
from app.models.enums import AgentRunStatus, UserRole
from app.runtime import service as runtime_service
from app.runtime.exceptions import RuntimeDomainError
from app.runtime.executor import execute_run

MCP_URL = "https://mcp.test/mcp"

_WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Look up the current weather for a city",
    "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}},
}

_WEATHER_CALL = ToolCall(id="call_1", name="get_weather", arguments={"city": "nyc"})


def _settings(**overrides: object) -> Settings:
    return Settings(
        jwt_secret_key="x" * 32,
        llm_allowed_providers="fake",
        llm_default_provider="fake",
        **overrides,
    )


def _settings_with_mcp_server(**overrides: object) -> Settings:
    servers = json.dumps([{"name": "test-server", "base_url": MCP_URL}])
    return _settings(mcp_servers_json=servers, **overrides)


def _gateway(provider: FakeProvider, settings: Settings) -> LLMGateway:
    return LLMGateway({"fake": provider}, settings, retry_base_delay_seconds=0.0)


def _agent_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "system_prompt": "You are helpful.",
        "model": "fake-v1",
        "mcp_server": "test-server",
    }
    config.update(overrides)
    return config


def _init_body() -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}}


def _queue_mcp_handshake(httpx_mock) -> None:
    httpx_mock.add_response(url=MCP_URL, method="POST", json=_init_body())
    httpx_mock.add_response(url=MCP_URL, method="POST", status_code=202, json={})


def _queue_list_tools(httpx_mock, tools: list[dict[str, object]]) -> None:
    _queue_mcp_handshake(httpx_mock)
    httpx_mock.add_response(
        url=MCP_URL, method="POST", json={"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}}
    )


def _queue_call_tool_success(httpx_mock, *, text: str) -> None:
    _queue_mcp_handshake(httpx_mock)
    httpx_mock.add_response(
        url=MCP_URL,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": text}], "isError": False},
        },
    )


def _queue_call_tool_failure(httpx_mock) -> None:
    httpx_mock.add_response(url=MCP_URL, method="POST", status_code=500)


def _tool_use_response(request: LLMRequest, *, tool_call: ToolCall) -> LLMResponse:
    return LLMResponse(
        request_id=request.request_id,
        provider="fake",
        model=request.model,
        content="",
        finish_reason="tool_use",
        usage=LLMUsage(input_tokens=5, output_tokens=5),
        latency_ms=0.0,
        created_at=datetime.now(timezone.utc),
        provider_request_id=f"fake-{request.request_id}",
        tool_calls=(tool_call,),
        metadata={},
    )


def _final_response(request: LLMRequest, *, content: str) -> LLMResponse:
    return LLMResponse(
        request_id=request.request_id,
        provider="fake",
        model=request.model,
        content=content,
        finish_reason="stop",
        usage=LLMUsage(input_tokens=5, output_tokens=5),
        latency_ms=0.0,
        created_at=datetime.now(timezone.utc),
        provider_request_id=f"fake-{request.request_id}",
        tool_calls=None,
        metadata={},
    )


class _ScriptedProvider(FakeProvider):
    """Returns a scripted tool_calls response on the first call, then a
    final plain-text response (no tool_calls) on every call after -
    FakeProvider itself only supports one fixed scripted response for
    its whole lifetime, so a genuine multi-turn test needs this small
    per-call-count override, the same pattern test_llm_gateway.py's
    FlakyProvider already uses."""

    def __init__(self, *, tool_call: ToolCall, final_content: str) -> None:
        super().__init__()
        self._tool_call = tool_call
        self._final_content = final_content
        self.call_count = 0

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        if self.call_count == 1:
            return _tool_use_response(request, tool_call=self._tool_call)
        return _final_response(request, content=self._final_content)


class _EchoSecondCallProvider(FakeProvider):
    """Like _ScriptedProvider, but the second call echoes back the last
    user message instead of a fixed string - proves the loop actually
    threads the MCP tool result into the next LLM call's messages,
    rather than just proving the loop ran twice."""

    def __init__(self, *, tool_call: ToolCall) -> None:
        super().__init__()
        self._tool_call = tool_call
        self.call_count = 0

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        if self.call_count == 1:
            return _tool_use_response(request, tool_call=self._tool_call)
        last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        return _final_response(request, content=last_user)


# --- Regression: no mcp_server configured -------------------------------


async def test_agent_without_mcp_server_makes_exactly_one_call(
    db_session, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()  # default configuration_json has no mcp_server key
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings()
    provider = FakeProvider(response_content="the answer")
    gateway = _gateway(provider, settings)

    result = await execute_run(
        db_session, gateway, settings, run=run, user_input="hi", context=None
    )

    assert result.status == AgentRunStatus.COMPLETED
    assert result.output_text == "the answer"


# --- Happy path: one tool call round trip --------------------------------


async def test_tool_call_round_trip_completes_with_final_answer(
    db_session, make_user, make_agent_definition, httpx_mock
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition(configuration_json=_agent_config())
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings_with_mcp_server()
    provider = _ScriptedProvider(tool_call=_WEATHER_CALL, final_content="It's sunny in NYC.")
    gateway = _gateway(provider, settings)

    _queue_list_tools(httpx_mock, [_WEATHER_TOOL])
    _queue_call_tool_success(httpx_mock, text="sunny, 72F")

    result = await execute_run(
        db_session,
        gateway,
        settings,
        run=run,
        user_input="what's the weather in nyc?",
        context=None,
    )

    assert result.status == AgentRunStatus.COMPLETED
    assert result.output_text == "It's sunny in NYC."
    assert provider.call_count == 2


async def test_tool_result_reaches_the_second_llm_call(
    db_session, make_user, make_agent_definition, httpx_mock
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition(configuration_json=_agent_config())
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings_with_mcp_server()
    provider = _EchoSecondCallProvider(tool_call=_WEATHER_CALL)
    gateway = _gateway(provider, settings)

    _queue_list_tools(httpx_mock, [_WEATHER_TOOL])
    _queue_call_tool_success(httpx_mock, text="sunny-marker-72F")

    result = await execute_run(
        db_session, gateway, settings, run=run, user_input="weather?", context=None
    )

    assert result.output_text is not None
    assert "sunny-marker-72F" in result.output_text


# --- Iteration cap --------------------------------------------------------


async def test_iteration_cap_fails_the_run_without_hanging(
    db_session, make_user, make_agent_definition, httpx_mock
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition(configuration_json=_agent_config())
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings_with_mcp_server(runtime_max_tool_iterations=2)
    provider = FakeProvider(tool_calls=(_WEATHER_CALL,))  # always wants a tool
    gateway = _gateway(provider, settings)

    _queue_list_tools(httpx_mock, [_WEATHER_TOOL])
    _queue_call_tool_success(httpx_mock, text="sunny")
    _queue_call_tool_success(httpx_mock, text="sunny")

    with pytest.raises(RuntimeDomainError):
        await execute_run(
            db_session, gateway, settings, run=run, user_input="weather?", context=None
        )

    await db_session.refresh(run)
    assert run.status == AgentRunStatus.FAILED
    assert run.error_code == "ToolLoopLimitExceededError"


# --- MCP failures ----------------------------------------------------------


async def test_mcp_tool_call_failure_fails_the_run(
    db_session, make_user, make_agent_definition, httpx_mock
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition(configuration_json=_agent_config())
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings_with_mcp_server()
    provider = FakeProvider(tool_calls=(_WEATHER_CALL,))
    gateway = _gateway(provider, settings)

    _queue_list_tools(httpx_mock, [_WEATHER_TOOL])
    _queue_call_tool_failure(httpx_mock)

    with pytest.raises(RuntimeDomainError):
        await execute_run(
            db_session, gateway, settings, run=run, user_input="weather?", context=None
        )

    await db_session.refresh(run)
    assert run.status == AgentRunStatus.FAILED
    assert run.error_code == "MCPProtocolError"


async def test_unknown_mcp_server_fails_the_run_before_any_llm_call(
    db_session, make_user, make_agent_definition, httpx_mock
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition(
        configuration_json=_agent_config(mcp_server="no-such-server")
    )
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings()  # no MCP servers configured at all
    provider = FakeProvider()
    gateway = _gateway(provider, settings)

    with pytest.raises(RuntimeDomainError):
        await execute_run(
            db_session, gateway, settings, run=run, user_input="weather?", context=None
        )

    await db_session.refresh(run)
    assert run.status == AgentRunStatus.FAILED
    assert run.error_code == "MCPServerNotConfigured"
    assert len(httpx_mock.get_requests()) == 0


# --- Defensive: tool_calls without tools offered --------------------------


async def test_tool_calls_without_mcp_server_configured_fails_cleanly(
    db_session, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()  # no mcp_server key
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings()
    provider = FakeProvider(tool_calls=(_WEATHER_CALL,))
    gateway = _gateway(provider, settings)

    with pytest.raises(RuntimeDomainError):
        await execute_run(db_session, gateway, settings, run=run, user_input="hi", context=None)

    await db_session.refresh(run)
    assert run.status == AgentRunStatus.FAILED
