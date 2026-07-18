"""Deterministic fake LLM provider (Sprint P5).

Makes no network calls and requires no credentials - used by the test
suite, and by the "verify the fake provider through the real API without
external credentials" quality gate: `POST /api/v1/llm/generate` with
`{"provider": "fake", ...}` works out of the box on a fresh checkout.

Deterministic: the same request always produces the same content, so
tests can assert on exact output instead of just "some string came
back".
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

from app.llm.base import LLMProvider
from app.llm.exceptions import LLMError
from app.llm.models import LLMRequest, LLMResponse, LLMUsage, ProviderHealth, StreamChunk, ToolCall

DEFAULT_MODEL = "fake-v1"


def _deterministic_content(request: LLMRequest) -> str:
    last_user = next(
        (m.content for m in reversed(request.messages) if m.role == "user"), ""
    )
    return f"[fake:{request.model}] {last_user}"


def _count_tokens(text: str) -> int:
    """A simple, deterministic word-count heuristic - not a real
    tokenizer. Good enough for the fake provider's purpose (exercising
    the gateway's usage/cost-accounting plumbing without a network call
    or a real tokenizer dependency)."""
    return len(text.split())


class FakeProvider(LLMProvider):
    """`fail_with`: pass an already-constructed app.llm.exceptions.LLMError
    instance to make every call raise it - lets gateway/retry-policy
    tests force a specific, deterministic failure mode (e.g.
    `FakeProvider(fail_with=LLMTimeoutError("fake"))`) without simulating
    real network conditions.

    `response_content`: override the deterministic echo content with a
    fixed string - used by structured-output tests to control exactly
    what "the provider returned" (including deliberately malformed JSON)
    without needing a second fake provider variant.

    `tool_calls`: script a fixed set of tool calls on the returned
    response (and force `finish_reason` to `"tool_use"`) - used by P9A's
    tool-calling tests to control exactly what "the model requested"
    without a network-bound provider.
    """

    name = "fake"

    def __init__(
        self,
        *,
        fail_with: LLMError | None = None,
        response_content: str | None = None,
        tool_calls: tuple[ToolCall, ...] | None = None,
    ) -> None:
        self._fail_with = fail_with
        self._response_content = response_content
        self._tool_calls = tool_calls

    def _content_for(self, request: LLMRequest) -> str:
        if self._response_content is not None:
            return self._response_content
        return _deterministic_content(request)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if self._fail_with is not None:
            raise self._fail_with

        content = self._content_for(request)
        input_tokens = _count_tokens(request.system_prompt or "") + sum(
            _count_tokens(m.content) for m in request.messages
        )
        output_tokens = _count_tokens(content)

        return LLMResponse(
            request_id=request.request_id,
            provider=self.name,
            model=request.model or DEFAULT_MODEL,
            content=content,
            finish_reason="tool_use" if self._tool_calls else "stop",
            usage=LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            latency_ms=0.0,
            created_at=datetime.now(timezone.utc),
            provider_request_id=f"fake-{request.request_id}",
            tool_calls=self._tool_calls,
            metadata={},
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        if self._fail_with is not None:
            raise self._fail_with
        content = self._content_for(request)
        yield StreamChunk(request_id=request.request_id, delta=content)
        yield StreamChunk(request_id=request.request_id, delta="", finish_reason="stop")

    async def health_check(self) -> ProviderHealth:
        if self._fail_with is not None:
            return ProviderHealth(
                provider=self.name,
                status="unavailable",
                detail=type(self._fail_with).__name__,
            )
        return ProviderHealth(provider=self.name, status="reachable", latency_ms=0.0)
