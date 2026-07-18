"""LLM Gateway API endpoints (Sprint P5).

Authorization matches the sprint spec's matrix: viewer can read
(providers/models/health/own request metadata), operator adds generate,
admin adds all current access plus any user's request metadata.
Unauthenticated is 401 everywhere - enforced by app/api/deps.py's
require_roles, the same dependency the Product and Asset APIs use.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.config import Settings, get_settings
from app.database.session import get_db
from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMEmbeddingNotSupported,
    LLMError,
    LLMInvalidRequest,
    LLMProviderNotConfigured,
    LLMProviderUnavailable,
    LLMRateLimitError,
    LLMResponseError,
    LLMStructuredOutputError,
    LLMTimeoutError,
    LLMUnsupportedModel,
)
from app.llm.factory import get_llm_gateway
from app.llm.gateway import LLMGateway
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.llm import (
    EmbedRequest,
    EmbedResponse,
    GenerateRequest,
    GenerateResponse,
    LLMRequestRead,
    ModelsResponse,
    ProviderHealthInfo,
    ProviderInfo,
)
from app.services import llm_service
from app.services.exceptions import LLMRequestNotFoundError

router = APIRouter(prefix="/llm", tags=["llm"])

_can_generate = require_roles(UserRole.OPERATOR, UserRole.ADMIN)
_can_read = require_roles(UserRole.VIEWER, UserRole.OPERATOR, UserRole.ADMIN)


def _map_llm_error(exc: LLMError) -> HTTPException:
    """The one place an app.llm.exceptions.LLMError becomes an HTTP
    status code - never a raw provider stack trace or secret, per the
    Error Model section's "Map these to controlled API responses"."""
    if isinstance(exc, LLMProviderNotConfigured):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, LLMAuthenticationError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="upstream LLM provider authentication failed",
        )
    if isinstance(exc, LLMRateLimitError):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="upstream LLM provider rate limit exceeded",
        )
    if isinstance(exc, LLMTimeoutError):
        return HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="upstream LLM provider request timed out",
        )
    if isinstance(exc, LLMProviderUnavailable):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="upstream LLM provider is unavailable"
        )
    if isinstance(exc, LLMUnsupportedModel):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, LLMEmbeddingNotSupported):
        # Sprint P10A: a well-formed request the chosen provider simply
        # can't fulfill (e.g. Anthropic has no embeddings API) - same
        # status family as LLMUnsupportedModel above, not a 503, since
        # the provider itself is perfectly reachable/configured.
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, LLMStructuredOutputError):
        # Never include exc.raw_text (the provider's raw response) in an
        # API error body - see the Structured Output section's "do not
        # leak raw responses in production errors".
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="structured output validation failed",
        )
    if isinstance(exc, LLMInvalidRequest):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, LLMResponseError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="upstream LLM provider returned an unexpected response",
        )
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="LLM provider error")  # pragma: no cover


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    payload: GenerateRequest,
    db: AsyncSession = Depends(get_db),
    gateway: LLMGateway = Depends(get_llm_gateway),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(_can_generate),
) -> GenerateResponse:
    try:
        return await llm_service.generate_and_persist(
            db, gateway, settings, payload=payload, user_id=current_user.id
        )
    except LLMError as exc:
        raise _map_llm_error(exc) from exc


@router.post("/embed", response_model=EmbedResponse)
async def embed(
    payload: EmbedRequest,
    db: AsyncSession = Depends(get_db),
    gateway: LLMGateway = Depends(get_llm_gateway),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(_can_generate),
) -> EmbedResponse:
    """Sprint P10A. Same auth bar as /generate - embeddings cost real
    provider tokens too, once a real (non-fake) provider is selected."""
    try:
        return await llm_service.embed_and_persist(
            db, gateway, settings, payload=payload, user_id=current_user.id
        )
    except LLMError as exc:
        raise _map_llm_error(exc) from exc


@router.get("/providers", response_model=list[ProviderInfo], dependencies=[Depends(_can_read)])
async def list_providers(
    gateway: LLMGateway = Depends(get_llm_gateway),
    settings: Settings = Depends(get_settings),
) -> list[ProviderInfo]:
    return llm_service.list_providers(gateway, settings)


@router.get("/models", response_model=ModelsResponse, dependencies=[Depends(_can_read)])
async def list_models(
    gateway: LLMGateway = Depends(get_llm_gateway),
    settings: Settings = Depends(get_settings),
) -> ModelsResponse:
    return ModelsResponse(
        providers=llm_service.list_providers(gateway, settings),
        aliases=llm_service.list_model_aliases(settings),
    )


@router.get("/health", response_model=list[ProviderHealthInfo], dependencies=[Depends(_can_read)])
async def health(gateway: LLMGateway = Depends(get_llm_gateway)) -> list[ProviderHealthInfo]:
    return await llm_service.get_health(gateway)


@router.get("/requests/{request_id}", response_model=LLMRequestRead)
async def get_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_can_read),
) -> LLMRequestRead:
    try:
        record = await llm_service.get_request(db, request_id, current_user)
    except LLMRequestNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return LLMRequestRead.model_validate(record)
