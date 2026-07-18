"""Internal, framework-agnostic LLM Gateway data structures (Sprint P5).

Plain dataclasses, not Pydantic models: app/llm/ must not import FastAPI
or SQLAlchemy (per the sprint spec), and these types are shared by
app/llm/base.py, app/llm/gateway.py, and every provider adapter under
app/llm/providers/. app/schemas/llm.py holds the separate, Pydantic-based
API request/response schemas FastAPI validates against; app/api/v1/llm.py
converts between the two at the boundary - the same split already used
for storage (app/storage/base.py's StorageMetadata vs
app/schemas/asset.py's AssetRead).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

MessageRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: MessageRole
    content: str


@dataclass(frozen=True)
class ToolDefinition:
    """A single tool an LLM may call, in provider-agnostic form. Each
    provider adapter (app/llm/providers/*.py) serializes this into its
    own wire format (Anthropic's `input_schema`, OpenAI's
    `function.parameters`, ...) - callers never build a provider-specific
    tool payload themselves."""

    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation an LLM requested, normalized the same
    way LLMResponse itself is - identical shape regardless of which
    provider actually served the request. `arguments` is always a parsed
    dict, even for providers (OpenAI) that return it as a JSON string on
    the wire."""

    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ResponseFormat:
    """Requests structured (JSON) output. `json_schema` is a plain JSON
    Schema document (the draft python-jsonschema understands).
    app/llm/gateway.py validates the provider's returned text against it
    after the call completes, regardless of whether the provider itself
    has native JSON-mode support - see app/llm/gateway.py's
    _validate_structured_output."""

    json_schema: dict[str, object]
    name: str = "response"


@dataclass(frozen=True)
class LLMRequest:
    """Provider-agnostic request. Never carries a resolved API key or any
    other secret - each provider adapter reads its own credentials from
    its constructor args (see app/llm/factory.py), not from this object,
    so a request can be logged/persisted without a secret ever being in
    it in the first place."""

    request_id: str
    model: str
    messages: tuple[Message, ...]
    provider: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    response_format: ResponseFormat | None = None
    tools: tuple[ToolDefinition, ...] | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class LLMResponse:
    """Normalized response - identical shape regardless of which
    provider actually served the request. Never carries a secret or a
    raw provider exception object; see LLMResponseError for the failure
    path's equivalent guarantee."""

    request_id: str
    provider: str
    model: str
    content: str
    finish_reason: str
    usage: LLMUsage
    latency_ms: float
    created_at: datetime
    provider_request_id: str | None = None
    estimated_cost_usd: Decimal | None = None
    tool_calls: tuple[ToolCall, ...] | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class StreamChunk:
    request_id: str
    delta: str
    finish_reason: str | None = None


ProviderHealthStatus = Literal["configured", "reachable", "unavailable", "not_configured"]


@dataclass(frozen=True)
class ProviderHealth:
    """`configured`: credentials present, not actively probed.
    `reachable`: an actual health probe succeeded. `unavailable`: probed
    and failed (network/5xx/timeout). `not_configured`: no credentials -
    see app/llm/gateway.py.health_check, which never lets one provider's
    failure raise out and take down the whole health endpoint."""

    provider: str
    status: ProviderHealthStatus
    detail: str | None = None
    latency_ms: float | None = None
