"""Anthropic Messages API adapter (Sprint P5).

Talks to Anthropic's REST API directly via httpx rather than the
`anthropic` SDK - one HTTP dependency instead of two, and it keeps the
adapter thin enough to unit test by mocking httpx responses (see
tests/test_llm_anthropic_adapter.py) instead of a heavier SDK client.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx

from app.llm.base import LLMProvider
from app.llm.exceptions import LLMProviderUnavailable, LLMResponseError, LLMTimeoutError
from app.llm.models import LLMRequest, LLMResponse, LLMUsage, ProviderHealth, StreamChunk, ToolCall
from app.llm.providers._shared import map_http_error

_API_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 1024


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        # x-api-key never appears in any exception message this adapter
        # raises - every app.llm.exceptions.LLMError subclass takes a
        # provider name, not a credential.
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }

    def _build_payload(self, request: LLMRequest) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": request.model,
            "max_tokens": request.max_tokens or _DEFAULT_MAX_TOKENS,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in request.messages
                if m.role != "system"
            ],
        }
        system_parts = [m.content for m in request.messages if m.role == "system"]
        system_prompt = request.system_prompt or ("\n\n".join(system_parts) or None)
        if system_prompt:
            payload["system"] = system_prompt
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.response_format is not None:
            # The Messages API has no formal response_format field - the
            # gateway (app/llm/gateway.py) is the authoritative validator
            # for structured output regardless of provider, so here we
            # only strengthen the instruction the model receives.
            existing_system = str(payload.get("system", ""))
            schema_hint = (
                "Respond with ONLY a single JSON object matching this JSON Schema, "
                "no prose, no markdown code fences:\n"
                f"{request.response_format.json_schema}"
            )
            payload["system"] = f"{existing_system}\n\n{schema_hint}".strip()
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in request.tools
            ]
        return payload

    async def generate(self, request: LLMRequest) -> LLMResponse:
        payload = self._build_payload(request)
        timeout = request.timeout_seconds or self._timeout_seconds
        started = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self._base_url}/v1/messages", headers=self._headers(), json=payload
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(self.name) from exc
        except httpx.RequestError as exc:
            raise LLMProviderUnavailable(self.name, str(exc)) from exc

        latency_ms = (time.monotonic() - started) * 1000

        if response.status_code != 200:
            raise map_http_error(self.name, request.model, response)

        try:
            body = response.json()
            text_blocks = [
                block["text"] for block in body["content"] if block.get("type") == "text"
            ]
            content = "".join(text_blocks)
            tool_use_blocks = [
                block for block in body["content"] if block.get("type") == "tool_use"
            ]
            tool_calls = (
                tuple(
                    ToolCall(
                        id=str(block["id"]),
                        name=str(block["name"]),
                        arguments=dict(block.get("input") or {}),
                    )
                    for block in tool_use_blocks
                )
                if tool_use_blocks
                else None
            )
            usage = body["usage"]
            input_tokens = int(usage["input_tokens"])
            output_tokens = int(usage["output_tokens"])
            finish_reason = str(body.get("stop_reason") or "stop")
            provider_request_id = body.get("id")
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMResponseError(self.name, str(exc)) from exc

        return LLMResponse(
            request_id=request.request_id,
            provider=self.name,
            model=request.model,
            content=content,
            finish_reason=finish_reason,
            usage=LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            latency_ms=latency_ms,
            created_at=datetime.now(timezone.utc),
            provider_request_id=provider_request_id,
            tool_calls=tool_calls,
            metadata={},
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        # Sprint P5 scope: the gateway's typed streaming interface must
        # exist, but the public streaming HTTP endpoint is explicitly
        # optional and not exposed this sprint (see app/api/v1/llm.py) -
        # real SSE parsing against Anthropic's streaming API is left for
        # the sprint that turns it on. This still satisfies the
        # interface today by delegating to the non-streaming call and
        # yielding the result as a single chunk, rather than raising
        # NotImplementedError.
        response = await self.generate(request)
        yield StreamChunk(
            request_id=request.request_id,
            delta=response.content,
            finish_reason=response.finish_reason,
        )

    async def health_check(self) -> ProviderHealth:
        # GET /v1/models is a lightweight, tokenless reachability +
        # credential check - deliberately not a real generate() call,
        # which would cost real tokens just to answer "is this up".
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/v1/models", headers=self._headers())
        except httpx.TimeoutException:
            return ProviderHealth(provider=self.name, status="unavailable", detail="timeout")
        except httpx.RequestError as exc:
            return ProviderHealth(provider=self.name, status="unavailable", detail=str(exc))

        latency_ms = (time.monotonic() - started) * 1000
        if response.status_code in (401, 403):
            return ProviderHealth(
                provider=self.name,
                status="unavailable",
                detail="authentication failed",
                latency_ms=latency_ms,
            )
        if response.status_code >= 500:
            return ProviderHealth(
                provider=self.name,
                status="unavailable",
                detail=f"HTTP {response.status_code}",
                latency_ms=latency_ms,
            )
        return ProviderHealth(provider=self.name, status="reachable", latency_ms=latency_ms)
