"""LLM Gateway service layer (Sprint P5): bridges app.llm.gateway.LLMGateway
(business logic, provider-agnostic) with the llm_requests table
(audit/usage persistence) and this app's authorization model.

app/api/v1/llm.py never imports app.llm.gateway or app.llm.providers
directly - it only calls into this module (plus app.llm.exceptions,
which every LLMError-mapping branch needs to catch) and
app.services.exceptions for domain errors.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.exceptions import LLMError, LLMStructuredOutputError
from app.llm.gateway import LLMGateway
from app.llm.models import EmbeddingRequest, LLMRequest, Message, ResponseFormat, ToolCall, ToolDefinition
from app.llm.routing import default_model_for, resolve_model, resolve_provider_name
from app.models.enums import LLMRequestStatus, UserRole
from app.models.llm_request import LLMRequestRecord
from app.models.user import User
from app.schemas.llm import (
    EmbedRequest,
    EmbedResponse,
    GenerateRequest,
    GenerateResponse,
    ModelAliasInfo,
    ProviderHealthInfo,
    ProviderInfo,
    ResponseFormatIn,
    ToolCallOut,
    ToolDefinitionIn,
)
from app.services.exceptions import LLMRequestNotFoundError


def _to_response_format(data: ResponseFormatIn | None) -> ResponseFormat | None:
    if data is None:
        return None
    if data.mode != "json_schema":
        raise LLMStructuredOutputError(f"Unsupported structured output mode: {data.mode!r}")
    return ResponseFormat(json_schema=data.json_schema, name=data.name)


def _to_tools(data: list[ToolDefinitionIn] | None) -> tuple[ToolDefinition, ...] | None:
    if not data:
        return None
    return tuple(
        ToolDefinition(name=t.name, description=t.description, input_schema=t.input_schema)
        for t in data
    )


def _tool_calls_to_schema(tool_calls: tuple[ToolCall, ...] | None) -> list[ToolCallOut] | None:
    if not tool_calls:
        return None
    return [ToolCallOut(id=c.id, name=c.name, arguments=c.arguments) for c in tool_calls]


async def generate_and_persist(
    db: AsyncSession,
    gateway: LLMGateway,
    settings: Settings,
    *,
    payload: GenerateRequest,
    user_id: uuid.UUID,
) -> GenerateResponse:
    """Persists a PENDING row before the provider call happens (so a row
    exists even if the process crashes mid-call), then updates it to
    SUCCEEDED or FAILED once the call resolves - or straight to FAILED if
    routing/request validation itself fails before any provider is ever
    contacted. Never stores payload.messages or the provider's response
    content anywhere - see app/models/llm_request.py's module docstring.
    """
    request_id = str(uuid.uuid4())

    try:
        resolved_provider = resolve_provider_name(payload.provider, settings)
        resolved_model = resolve_model(resolved_provider, payload.model, settings)
        response_format = _to_response_format(payload.response_format)
    except LLMError as exc:
        record = LLMRequestRecord(
            id=uuid.UUID(request_id),
            user_id=user_id,
            provider=payload.provider or "unresolved",
            model=payload.model,
            status=LLMRequestStatus.FAILED,
            request_metadata_json=dict(payload.metadata),
            error_code=type(exc).__name__,
            completed_at=datetime.now(timezone.utc),
        )
        db.add(record)
        await db.commit()
        raise

    max_tokens = settings.llm_max_output_tokens
    if payload.max_tokens is not None:
        max_tokens = min(payload.max_tokens, settings.llm_max_output_tokens)

    llm_request = LLMRequest(
        request_id=request_id,
        model=resolved_model,
        messages=tuple(Message(role=m.role, content=m.content) for m in payload.messages),
        provider=resolved_provider,
        system_prompt=payload.system_prompt,
        temperature=payload.temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        tools=_to_tools(payload.tools),
        metadata=payload.metadata,
    )

    record = LLMRequestRecord(
        id=uuid.UUID(request_id),
        user_id=user_id,
        provider=resolved_provider,
        model=resolved_model,
        status=LLMRequestStatus.PENDING,
        request_metadata_json=dict(payload.metadata),
    )
    db.add(record)
    await db.commit()

    started = time.monotonic()
    try:
        response = await gateway.generate(llm_request)
    except LLMError as exc:
        record.status = LLMRequestStatus.FAILED
        record.error_code = type(exc).__name__
        record.latency_ms = int((time.monotonic() - started) * 1000)
        record.completed_at = datetime.now(timezone.utc)
        db.add(record)
        await db.commit()
        raise

    record.status = LLMRequestStatus.SUCCEEDED
    record.input_tokens = response.usage.input_tokens
    record.output_tokens = response.usage.output_tokens
    record.total_tokens = response.usage.total_tokens
    record.estimated_cost_usd = response.estimated_cost_usd
    record.latency_ms = int(response.latency_ms)
    record.response_metadata_json = dict(response.metadata)
    record.completed_at = datetime.now(timezone.utc)
    db.add(record)
    await db.commit()

    return GenerateResponse(
        request_id=response.request_id,
        provider=response.provider,
        model=response.model,
        content=response.content,
        finish_reason=response.finish_reason,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        total_tokens=response.usage.total_tokens,
        estimated_cost_usd=response.estimated_cost_usd,
        latency_ms=response.latency_ms,
        created_at=response.created_at,
        provider_request_id=response.provider_request_id,
        tool_calls=_tool_calls_to_schema(response.tool_calls),
        metadata=response.metadata,
    )


async def embed_and_persist(
    db: AsyncSession,
    gateway: LLMGateway,
    settings: Settings,
    *,
    payload: EmbedRequest,
    user_id: uuid.UUID,
) -> EmbedResponse:
    """Sprint P10A. Mirrors generate_and_persist's shape exactly: persist
    a PENDING row before the provider call, update to SUCCEEDED/FAILED
    after. Reuses LLMRequestRecord's existing columns as-is - no new
    column, no migration: output_tokens is always 0 and total_tokens
    equals input_tokens, since an embedding call has no "output" in the
    generate() sense. Never stores payload.input anywhere - same
    no-content-persistence guarantee as generate_and_persist.
    """
    request_id = str(uuid.uuid4())
    normalized_input = (
        (payload.input,) if isinstance(payload.input, str) else tuple(payload.input)
    )

    try:
        resolved_provider = resolve_provider_name(payload.provider, settings)
        resolved_model = resolve_model(resolved_provider, payload.model, settings)
    except LLMError as exc:
        record = LLMRequestRecord(
            id=uuid.UUID(request_id),
            user_id=user_id,
            provider=payload.provider or "unresolved",
            model=payload.model,
            status=LLMRequestStatus.FAILED,
            request_metadata_json=dict(payload.metadata),
            error_code=type(exc).__name__,
            completed_at=datetime.now(timezone.utc),
        )
        db.add(record)
        await db.commit()
        raise

    embedding_request = EmbeddingRequest(
        request_id=request_id,
        model=resolved_model,
        input=normalized_input,
        provider=resolved_provider,
        metadata=payload.metadata,
    )

    record = LLMRequestRecord(
        id=uuid.UUID(request_id),
        user_id=user_id,
        provider=resolved_provider,
        model=resolved_model,
        status=LLMRequestStatus.PENDING,
        request_metadata_json=dict(payload.metadata),
    )
    db.add(record)
    await db.commit()

    started = time.monotonic()
    try:
        response = await gateway.embed(embedding_request)
    except LLMError as exc:
        record.status = LLMRequestStatus.FAILED
        record.error_code = type(exc).__name__
        record.latency_ms = int((time.monotonic() - started) * 1000)
        record.completed_at = datetime.now(timezone.utc)
        db.add(record)
        await db.commit()
        raise

    record.status = LLMRequestStatus.SUCCEEDED
    record.input_tokens = response.usage.input_tokens
    record.output_tokens = response.usage.output_tokens
    record.total_tokens = response.usage.total_tokens
    record.estimated_cost_usd = response.estimated_cost_usd
    record.latency_ms = int(response.latency_ms)
    record.response_metadata_json = dict(response.metadata)
    record.completed_at = datetime.now(timezone.utc)
    db.add(record)
    await db.commit()

    return EmbedResponse(
        request_id=response.request_id,
        provider=response.provider,
        model=response.model,
        embeddings=[list(vec) for vec in response.embeddings],
        input_tokens=response.usage.input_tokens,
        estimated_cost_usd=response.estimated_cost_usd,
        latency_ms=response.latency_ms,
        created_at=response.created_at,
        provider_request_id=response.provider_request_id,
        metadata=response.metadata,
    )


async def get_request(
    db: AsyncSession, request_id: uuid.UUID, current_user: User
) -> LLMRequestRecord:
    """Admins may fetch any request's metadata; everyone else only their
    own - both a genuinely missing id and someone else's id raise the
    same LLMRequestNotFoundError, so the endpoint can't be used to probe
    for other users' request ids."""
    record = await db.get(LLMRequestRecord, request_id)
    if record is None:
        raise LLMRequestNotFoundError(request_id)
    if current_user.role != UserRole.ADMIN and record.user_id != current_user.id:
        raise LLMRequestNotFoundError(request_id)
    return record


def list_providers(gateway: LLMGateway, settings: Settings) -> list[ProviderInfo]:
    configured = gateway.configured_providers()
    return [
        ProviderInfo(
            name=name,
            configured=name in configured,
            default_model=default_model_for(name, settings),
        )
        for name in settings.llm_allowed_providers_list
    ]


def list_model_aliases(settings: Settings) -> list[ModelAliasInfo]:
    aliases: list[ModelAliasInfo] = []
    for provider, mapping in settings.llm_model_aliases_map.items():
        for alias, resolved_model in mapping.items():
            aliases.append(
                ModelAliasInfo(provider=provider, alias=alias, resolved_model=resolved_model)
            )
    return aliases


async def get_health(gateway: LLMGateway) -> list[ProviderHealthInfo]:
    results = await gateway.health_check()
    return [
        ProviderHealthInfo(
            provider=r.provider, status=r.status, detail=r.detail, latency_ms=r.latency_ms
        )
        for r in results
    ]
