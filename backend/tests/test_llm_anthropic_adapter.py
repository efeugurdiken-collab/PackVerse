"""Tests for app.llm.providers.anthropic.AnthropicProvider (Sprint P5).

Uses pytest-httpx's `httpx_mock` fixture to intercept the adapter's
httpx.AsyncClient calls - no real network access, no real Anthropic API
key required.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMEmbeddingNotSupported,
    LLMProviderUnavailable,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from app.llm.models import EmbeddingRequest, LLMRequest, Message, ToolDefinition
from app.llm.providers.anthropic import AnthropicProvider

BASE_URL = "https://api.anthropic.test"


def _provider() -> AnthropicProvider:
    return AnthropicProvider(api_key="test-anthropic-key", base_url=BASE_URL, timeout_seconds=5.0)


def _request(**overrides: object) -> LLMRequest:
    defaults: dict[str, object] = {
        "request_id": "req-1",
        "model": "claude-3-5-haiku-20241022",
        "messages": (Message(role="user", content="hi"),),
        "system_prompt": "be nice",
    }
    defaults.update(overrides)
    return LLMRequest(**defaults)  # type: ignore[arg-type]


def _success_body(
    *, text: str = "hello!", stop_reason: str = "end_turn", input_tokens: int = 5, output_tokens: int = 3
) -> dict[str, object]:
    return {
        "id": "msg_123",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


# --- Request mapping ------------------------------------------------------


async def test_request_mapping_sends_headers_model_system_and_messages(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{BASE_URL}/v1/messages", method="POST", json=_success_body())

    await _provider().generate(_request())

    sent = httpx_mock.get_requests()[0]
    assert sent.headers["x-api-key"] == "test-anthropic-key"
    assert sent.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(sent.content)
    assert body["model"] == "claude-3-5-haiku-20241022"
    assert body["system"] == "be nice"
    assert body["messages"] == [{"role": "user", "content": "hi"}]


async def test_request_mapping_never_includes_api_key_in_body(httpx_mock) -> None:
    httpx_mock.add_response(json=_success_body())

    await _provider().generate(_request())

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "test-anthropic-key" not in json.dumps(body)


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
            "name": "get_weather",
            "description": "Look up the current weather for a city",
            "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        }
    ]


# --- Response / usage mapping ----------------------------------------------


async def test_response_mapping_concatenates_text_blocks(httpx_mock) -> None:
    httpx_mock.add_response(
        json={
            "id": "msg_abc",
            "content": [{"type": "text", "text": "part1"}, {"type": "text", "text": "part2"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
    )

    response = await _provider().generate(_request())

    assert response.content == "part1part2"
    assert response.finish_reason == "end_turn"
    assert response.provider_request_id == "msg_abc"
    assert response.provider == "anthropic"


async def test_response_mapping_parses_tool_use_blocks(httpx_mock) -> None:
    httpx_mock.add_response(
        json={
            "id": "msg_tool",
            "content": [
                {"type": "text", "text": "let me check"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "get_weather",
                    "input": {"city": "nyc"},
                },
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
    )

    response = await _provider().generate(_request())

    assert response.finish_reason == "tool_use"
    assert response.tool_calls is not None
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.id == "toolu_1"
    assert call.name == "get_weather"
    assert call.arguments == {"city": "nyc"}


async def test_response_without_tool_use_blocks_has_no_tool_calls(httpx_mock) -> None:
    httpx_mock.add_response(json=_success_body())

    response = await _provider().generate(_request())

    assert response.tool_calls is None


async def test_usage_mapping(httpx_mock) -> None:
    httpx_mock.add_response(json=_success_body(input_tokens=7, output_tokens=9))

    response = await _provider().generate(_request())

    assert response.usage.input_tokens == 7
    assert response.usage.output_tokens == 9


# --- Error mapping --------------------------------------------------------


async def test_timeout_mapping(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.TimeoutException("timed out"))

    with pytest.raises(LLMTimeoutError):
        await _provider().generate(_request())


async def test_connection_error_mapping(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))

    with pytest.raises(LLMProviderUnavailable):
        await _provider().generate(_request())


async def test_rate_limit_mapping_parses_retry_after(httpx_mock) -> None:
    httpx_mock.add_response(
        status_code=429,
        headers={"retry-after": "3"},
        json={"error": {"type": "rate_limit_error", "message": "slow down"}},
    )

    with pytest.raises(LLMRateLimitError) as excinfo:
        await _provider().generate(_request())
    assert excinfo.value.retry_after_seconds == 3.0
    assert excinfo.value.retryable is True


async def test_auth_error_mapping(httpx_mock) -> None:
    httpx_mock.add_response(
        status_code=401, json={"error": {"type": "authentication_error", "message": "bad key"}}
    )

    with pytest.raises(LLMAuthenticationError) as excinfo:
        await _provider().generate(_request())
    assert excinfo.value.retryable is False


async def test_provider_error_mapping_for_5xx(httpx_mock) -> None:
    httpx_mock.add_response(
        status_code=529, json={"error": {"type": "overloaded_error", "message": "overloaded"}}
    )

    with pytest.raises(LLMProviderUnavailable) as excinfo:
        await _provider().generate(_request())
    assert excinfo.value.retryable is True


async def test_unexpected_response_shape_raises_response_error(httpx_mock) -> None:
    httpx_mock.add_response(json={"unexpected": "shape"})

    with pytest.raises(LLMResponseError):
        await _provider().generate(_request())


# --- Health check -----------------------------------------------------


async def test_health_check_reachable_on_200(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{BASE_URL}/v1/models", method="GET", json={"data": []})

    health = await _provider().health_check()

    assert health.status == "reachable"
    assert health.provider == "anthropic"


async def test_health_check_unavailable_on_auth_failure(httpx_mock) -> None:
    httpx_mock.add_response(url=f"{BASE_URL}/v1/models", method="GET", status_code=401)

    health = await _provider().health_check()

    assert health.status == "unavailable"


async def test_health_check_unavailable_on_timeout(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.TimeoutException("timed out"))

    health = await _provider().health_check()

    assert health.status == "unavailable"


# --- Embeddings (Sprint P10A: no embeddings API, raises immediately) ------


async def test_embed_raises_not_supported(httpx_mock) -> None:
    request = EmbeddingRequest(request_id="req-1", model="claude-3-5-haiku-20241022", input=("hi",))

    with pytest.raises(LLMEmbeddingNotSupported):
        await _provider().embed(request)

    # No HTTP call was ever attempted.
    assert len(httpx_mock.get_requests()) == 0
