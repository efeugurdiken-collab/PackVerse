"""Tests for app.llm.providers.openai_compatible.OpenAICompatibleProvider
(Sprint P5).

Uses pytest-httpx's `httpx_mock` fixture to intercept the adapter's
httpx.AsyncClient calls - no real network access, no real API key
required. Also exercises OpenRouter/local-server compatibility by
pointing base_url at an arbitrary host, since that's the whole point of
this adapter being generic.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from app.llm.models import LLMRequest, Message, ResponseFormat, ToolDefinition
from app.llm.providers.openai_compatible import OpenAICompatibleProvider

BASE_URL = "https://api.openai.test/v1"


def _provider(**overrides: object) -> OpenAICompatibleProvider:
    defaults: dict[str, object] = {
        "api_key": "test-openai-key",
        "base_url": BASE_URL,
        "timeout_seconds": 5.0,
    }
    defaults.update(overrides)
    return OpenAICompatibleProvider(**defaults)  # type: ignore[arg-type]


def _request(**overrides: object) -> LLMRequest:
    defaults: dict[str, object] = {
        "request_id": "req-1",
        "model": "gpt-4o-mini",
        "messages": (Message(role="user", content="hi"),),
        "system_prompt": "be nice",
    }
    defaults.update(overrides)
    return LLMRequest(**defaults)


def _success_body(
    *, content: str = "hello!", finish_reason: str = "stop", prompt_tokens: int = 5, completion_tokens: int = 3
) -> dict[str, object]:
    return {
        "id": "chatcmpl-123",
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


# --- Request mapping ------------------------------------------------------


async def test_request_mapping_sends_auth_header_and_system_message(httpx_mock) -> None:
    httpx_mock.add_response(
        url=f"{BASE_URL}/chat/completions", method="POST", json=_success_body()
    )

    await _provider().generate(_request())

    sent = httpx_mock.get_requests()[0]
    assert sent.headers["authorization"] == "Bearer test-openai-key"
    body = json.loads(sent.content)
    assert body["model"] == "gpt-4o-mini"
    assert body["messages"][0] == {"role": "system", "content": "be nice"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}


async def test_request_mapping_includes_organization_and_project_headers(httpx_mock) -> None:
    httpx_mock.add_response(json=_success_body())

    await _provider(organization="org-1", project="proj-1").generate(_request())

    sent = httpx_mock.get_requests()[0]
    assert sent.headers["openai-organization"] == "org-1"
    assert sent.headers["openai-project"] == "proj-1"


async def test_structured_output_request_includes_response_format(httpx_mock) -> None:
    httpx_mock.add_response(json=_success_body(content='{"a": 1}'))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}

    await _provider().generate(
        _request(response_format=ResponseFormat(json_schema=schema, name="answer"))
    )

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == schema
    assert body["response_format"]["json_schema"]["name"] == "answer"


async def test_request_without_tools_omits_tools_key(httpx_mock) -> None:
    httpx_mock.add_response(json=_success_body())

    await _provider().generate(_request())

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "tools" not in body


async def test_request_mapping_includes_tools_when_set(httpx_mock) -> None:
    httpx_mock.add_response(json=_success_body())
    tool = ToolDefinition(
        name="get_weather",
        description="Look up the current weather for a city",
        input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
    )

    await _provider().generate(_request(tools=(tool,)))

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Look up the current weather for a city",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]


# --- Response / usage mapping ----------------------------------------------


async def test_response_mapping(httpx_mock) -> None:
    httpx_mock.add_response(json=_success_body(content="the answer", finish_reason="stop"))

    response = await _provider().generate(_request())

    assert response.content == "the answer"
    assert response.finish_reason == "stop"
    assert response.provider == "openai"
    assert response.provider_request_id == "chatcmpl-123"


async def test_response_mapping_parses_tool_calls(httpx_mock) -> None:
    httpx_mock.add_response(
        json={
            "id": "chatcmpl-tool",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"city": "nyc"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
    )

    response = await _provider().generate(_request())

    assert response.finish_reason == "tool_calls"
    assert response.content == ""
    assert response.tool_calls is not None
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "get_weather"
    assert call.arguments == {"city": "nyc"}


async def test_response_without_tool_calls_has_none(httpx_mock) -> None:
    httpx_mock.add_response(json=_success_body())

    response = await _provider().generate(_request())

    assert response.tool_calls is None


async def test_usage_mapping(httpx_mock) -> None:
    httpx_mock.add_response(json=_success_body(prompt_tokens=11, completion_tokens=22))

    response = await _provider().generate(_request())

    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 22


# --- Error mapping --------------------------------------------------------


async def test_timeout_mapping(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.TimeoutException("timed out"))

    with pytest.raises(LLMTimeoutError):
        await _provider().generate(_request())


async def test_rate_limit_mapping(httpx_mock) -> None:
    httpx_mock.add_response(
        status_code=429,
        headers={"retry-after": "2"},
        json={"error": {"message": "rate limited", "type": "rate_limit_error"}},
    )

    with pytest.raises(LLMRateLimitError) as excinfo:
        await _provider().generate(_request())
    assert excinfo.value.retry_after_seconds == 2.0


async def test_auth_error_mapping(httpx_mock) -> None:
    httpx_mock.add_response(
        status_code=401, json={"error": {"message": "invalid api key", "type": "invalid_request_error"}}
    )

    with pytest.raises(LLMAuthenticationError):
        await _provider().generate(_request())


async def test_unexpected_response_shape_raises_response_error(httpx_mock) -> None:
    httpx_mock.add_response(json={"choices": []})

    with pytest.raises(LLMResponseError):
        await _provider().generate(_request())


# --- Health check -----------------------------------------------------


async def test_health_check_reachable_on_200(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{BASE_URL}/models", method="GET", json={"data": []})

    health = await _provider().health_check()

    assert health.status == "reachable"
    assert health.provider == "openai"


async def test_health_check_unavailable_on_5xx(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{BASE_URL}/models", method="GET", status_code=503)

    health = await _provider().health_check()

    assert health.status == "unavailable"
