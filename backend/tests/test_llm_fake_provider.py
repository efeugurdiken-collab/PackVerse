"""Unit tests for app.llm.providers.fake.FakeProvider (Sprint P5).

No network access - this is the provider whose entire purpose is to
work without any.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.llm.exceptions import LLMTimeoutError
from app.llm.models import LLMRequest, Message
from app.llm.providers.fake import FakeProvider


def _request(**overrides: Any) -> LLMRequest:
    defaults: dict[str, Any] = {
        "request_id": str(uuid.uuid4()),
        "model": "fake-v1",
        "messages": (Message(role="user", content="hello there"),),
    }
    defaults.update(overrides)
    return LLMRequest(**defaults)


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
