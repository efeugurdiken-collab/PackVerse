"""LLM Gateway API schemas (Sprint P5).

Pydantic v2 request/response models for /api/v1/llm/* - the HTTP-facing
counterpart to the framework-agnostic dataclasses in app/llm/models.py.
app/services/llm_service.py converts between the two at the boundary.

LLMRequestRead in particular never exposes prompt or generated content -
per the sprint's "Do not expose full prompt content in request-history
endpoints" - because app/models/llm_request.py never stores it in the
first place; there is nothing here to accidentally leak.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import LLMRequestStatus

MessageRole = Literal["system", "user", "assistant"]


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # `role` being a Literal (not `str`) is what makes "reject
    # unsupported roles" a plain 422 from Pydantic itself - no custom
    # validator needed.
    role: MessageRole
    content: str = Field(min_length=1)


class ResponseFormatIn(BaseModel):
    """`mode` is deliberately `str`, not `Literal["json_schema"]`: an
    unsupported mode should surface as the sprint's required controlled
    domain error (LLMStructuredOutputError -> 422 with a clear message),
    not a generic Pydantic validation error - see
    app/services/llm_service.py's _to_response_format."""

    model_config = ConfigDict(extra="forbid")

    mode: str = "json_schema"
    name: str = "response"
    json_schema: dict[str, object]


class ToolDefinitionIn(BaseModel):
    """A single tool the model may call (Sprint P9A). Serialized into
    each provider's own wire format by app/llm/providers/*.py - see
    app/llm/models.py's ToolDefinition, the framework-agnostic
    counterpart this converts to at the boundary."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, object]


class ToolCallOut(BaseModel):
    """A single tool invocation the model requested (Sprint P9A).
    `arguments` is always a parsed dict, even for providers that return
    it as a JSON string on the wire - see app/llm/models.py's ToolCall."""

    id: str
    name: str
    arguments: dict[str, object]


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str = Field(min_length=1)
    system_prompt: str | None = None
    messages: list[MessageIn] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    response_format: ResponseFormatIn | None = None
    tools: list[ToolDefinitionIn] | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class GenerateResponse(BaseModel):
    request_id: str
    provider: str
    model: str
    content: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: Decimal | None
    latency_ms: float
    created_at: datetime
    provider_request_id: str | None
    tool_calls: list[ToolCallOut] | None
    metadata: dict[str, object]


class EmbedRequest(BaseModel):
    """Sprint P10A. `input` accepts either a single string or a list -
    normalized to a tuple at the app.llm.models.EmbeddingRequest
    boundary (app/services/llm_service.py's embed_and_persist) so every
    provider adapter always deals with a batch of one-or-more, never a
    single-vs-many branch of its own."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str = Field(min_length=1)
    input: str | list[str]
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("input")
    @classmethod
    def validate_input_not_empty(cls, v: str | list[str]) -> str | list[str]:
        if isinstance(v, str):
            if not v:
                raise ValueError("input must not be empty")
            return v
        if not v:
            raise ValueError("input must contain at least one string")
        if any(not item for item in v):
            raise ValueError("input must not contain empty strings")
        return v


class EmbedResponse(BaseModel):
    request_id: str
    provider: str
    model: str
    embeddings: list[list[float]]
    input_tokens: int
    estimated_cost_usd: Decimal | None
    latency_ms: float
    created_at: datetime
    provider_request_id: str | None
    metadata: dict[str, object]


class ProviderInfo(BaseModel):
    name: str
    configured: bool
    default_model: str | None


class ModelAliasInfo(BaseModel):
    provider: str
    alias: str
    resolved_model: str


class ModelsResponse(BaseModel):
    providers: list[ProviderInfo]
    aliases: list[ModelAliasInfo]


class ProviderHealthInfo(BaseModel):
    provider: str
    status: str
    detail: str | None
    latency_ms: float | None


class LLMRequestRead(BaseModel):
    """No prompt/response content field anywhere in this schema, by
    design - see the module docstring."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    provider: str
    model: str
    status: LLMRequestStatus
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    estimated_cost_usd: Decimal | None
    latency_ms: int | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None
