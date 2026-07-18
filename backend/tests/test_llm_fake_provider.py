"""Unit tests for app.llm.providers.fake.FakeProvider (Sprint P5).

No network access - this is the provider whose entire purpose is to
work without any.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.llm.exceptions import LLMTimeoutError
from app.llm.models import EmbeddingRequest, LLMRequest, Message, ToolCall
from app.llm.providers.fake import FakeProvider


def _request(**overrides: Any) -> LLMRequest:
    defaults: dict[str, Any] = {
        "request_id": str(uuid.uuid4()),
        "model": "fake-v1",
        "messages": (Message(role="user", content="hello there"),),
    }
    defaults.update(overrides)
    return LLMRequest(**defaults)


def _embed_request(**overrides: Any) -> EmbeddingRequest:
    defaults: dict[str, Any] = {
        "request_id": str(uuid.uuid4()),
        "model": "fake-embed-v1",
        "input": ("hello there",),
    }
    defaults.update(overrides)
    return EmbeddingRequest(**defaults)


async def test_generate_is_deterministic_for_the_same_request() -> None:
    provider = FakeProvider()
    request = _request()

    first = await provider.generate(request)
    second = await provider.generate(request)

    assert first.content == second.content


async def test_generate_reports_deterministic_usage() -> None:
    provider = FakeProvider()
    request = _request(messages=(Message(role="user", content="one two three"),))

    response = await provider.generate(request)

    assert response.usage.input_tokens == 3
    assert response.usage.output_tokens > 0
    assert response.usage.total_tokens == (
        response.usage.input_tokens + response.usage.output_tokens
    )


async def test_fail_with_forces_a_specific_exception() -> None:
    provider = FakeProvider(fail_with=LLMTimeoutError("fake"))

    with pytest.raises(LLMTimeoutError):
        await provider.generate(_request())


async def test_health_check_reports_reachable_by_default() -> None:
    provider = FakeProvider()

    health = await provider.health_check()

    assert health.status == "reachable"
    assert health.provider == "fake"


async def test_health_check_reports_unavailable_when_fail_with_set() -> None:
    provider = FakeProvider(fail_with=LLMTimeoutError("fake"))

    health = await provider.health_check()

    assert health.status == "unavailable"


async def test_response_content_override() -> None:
    provider = FakeProvider(response_content='{"ok": true}')

    response = await provider.generate(_request())

    assert response.content == '{"ok": true}'


async def test_stream_yields_content_then_a_finish_chunk() -> None:
    provider = FakeProvider(response_content="hello")

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert chunks[0].delta == "hello"
    assert chunks[-1].finish_reason == "stop"


async def test_stream_raises_fail_with_immediately() -> None:
    provider = FakeProvider(fail_with=LLMTimeoutError("fake"))

    with pytest.raises(LLMTimeoutError):
        async for _ in provider.stream(_request()):
            pass


async def test_no_tool_calls_by_default() -> None:
    provider = FakeProvider()

    response = await provider.generate(_request())

    assert response.tool_calls is None
    assert response.finish_reason == "stop"


async def test_tool_calls_override_are_returned_verbatim() -> None:
    tool_call = ToolCall(id="call_1", name="get_weather", arguments={"city": "nyc"})
    provider = FakeProvider(tool_calls=(tool_call,))

    response = await provider.generate(_request())

    assert response.tool_calls == (tool_call,)
    assert response.finish_reason == "tool_use"


async def test_embed_returns_deterministic_vector_for_the_same_input() -> None:
    provider = FakeProvider()
    request = _embed_request()

    first = await provider.embed(request)
    second = await provider.embed(request)

    assert first.embeddings == second.embeddings
    assert len(first.embeddings) == 1
    assert len(first.embeddings[0]) > 0


async def test_embed_returns_different_vectors_for_different_input() -> None:
    provider = FakeProvider()

    a = await provider.embed(_embed_request(input=("hello",)))
    b = await provider.embed(_embed_request(input=("goodbye",)))

    assert a.embeddings != b.embeddings


async def test_embed_supports_batch_input() -> None:
    provider = FakeProvider()
    request = _embed_request(input=("one", "two", "three"))

    response = await provider.embed(request)

    assert len(response.embeddings) == 3
    assert response.embeddings[0] != response.embeddings[1]


async def test_embed_reports_usage() -> None:
    provider = FakeProvider()
    request = _embed_request(input=("one two three",))

    response = await provider.embed(request)

    assert response.usage.input_tokens == 3
    assert response.usage.output_tokens == 0
    assert response.usage.total_tokens == 3


async def test_embed_raises_fail_with_immediately() -> None:
    provider = FakeProvider(fail_with=LLMTimeoutError("fake"))

    with pytest.raises(LLMTimeoutError):
        await provider.embed(_embed_request())
