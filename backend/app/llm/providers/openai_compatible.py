"""OpenAI-compatible Chat Completions adapter (Sprint P5).

One generic adapter for OpenAI itself, OpenRouter, or any local
OpenAI-compatible server (vLLM, LM Studio, Ollama's OpenAI-compatible
endpoint, etc.) - the only thing that changes between them is
`base_url`/`api_key`, both configurable (see app/core/config.py's
OPENAI_* settings and app/llm/factory.py).
"""
from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx

from app.llm.base import LLMProvider
from app.llm.exceptions import LLMProviderUnavailable, LLMResponseError, LLMTimeoutError
from app.llm.models import LLMRequest, LLMResponse, LLMUsage, ProviderHealth, StreamChunk, ToolCall
from app.llm.providers._shared import map_http_error


class OpenAICompatibleProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        organization: str | None = None,
        project: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._organization = organization
        self._project = project
        self._timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        # The bearer token never appears in any exception message this
        # adapter raises - every app.llm.exceptions.LLMError subclass
        # takes a provider name, not a credential.
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._organization:
            headers["OpenAI-Organization"] = self._organization
        if self._project:
            headers["OpenAI-Project"] = self._project
        return headers

    def _build_payload(self, request: LLMRequest) -> dict[str, object]:
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.extend({"role": m.role, "content": m.content} for m in request.messages)

        payload: dict[str, object] = {"model": request.model, "messages": messages}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.response_format is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_format.name,
                    "schema": request.response_format.json_schema,
                    "strict": True,
                },
            }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
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
                    f"{self._base_url}/chat/completions", headers=self._headers(), json=payload
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
            choice = body["choices"][0]
            content = choice["message"]["content"] or ""
            finish_reason = str(choice.get("finish_reason") or "stop")
            usage = body.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
            provider_request_id = body.get("id")
            raw_tool_calls = choice["message"].get("tool_calls")
            tool_calls = (
                tuple(
                    ToolCall(
                        id=str(call["id"]),
                        name=str(call["function"]["name"]),
                        arguments=json.loads(call["function"]["arguments"] or "{}"),
                    )
                    for call in raw_tool_calls
                )
                if raw_tool_calls
                else None
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
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
        # Same scope decision as app/llm/providers/anthropic.py's
        # stream(): satisfies the required interface without a real SSE
        # implementation, since the public streaming endpoint is not
        # exposed this sprint.
        response = await self.generate(request)
        yield StreamChunk(
            request_id=request.request_id,
            delta=response.content,
            finish_reason=response.finish_reason,
        )

    async def health_check(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/models", headers=self._headers())
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
