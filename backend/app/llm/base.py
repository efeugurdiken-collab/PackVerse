"""Provider interface (Sprint P5).

Every concrete provider (app/llm/providers/anthropic.py,
openai_compatible.py, fake.py) implements this ABC. Business logic
(app/llm/gateway.py) depends only on this interface - it never imports a
concrete provider class directly, so adding a new provider never touches
gateway.py. Same pattern as app/storage/base.py's StorageBackend for the
storage layer.

No FastAPI or SQLAlchemy imports here, by design - see app/llm/models.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.llm.models import LLMRequest, LLMResponse, ProviderHealth, StreamChunk


class LLMProvider(ABC):
    """`name` is used as the provider registry key (app/llm/factory.py)
    and as the `provider` field on every LLMResponse/LLMRequestRecord
    row - keep it stable once a provider ships (e.g. "anthropic",
    "openai", "fake")."""

    name: str

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Raises an app.llm.exceptions.LLMError subclass on failure -
        never a raw httpx/SDK exception. See each adapter's own
        error-mapping section for the exact status-code -> exception
        table."""
        raise NotImplementedError

    @abstractmethod
    def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        """Typed streaming interface, required by the gateway even
        though the public HTTP streaming endpoint is optional this
        sprint and not exposed (see app/api/v1/llm.py). Implementations
        are async generators; this is deliberately a plain (non-async)
        method returning an AsyncIterator, so callers iterate the result
        directly rather than awaiting the call itself."""
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        """Must never raise - a provider that can't be reached reports
        status="unavailable" with `detail` explaining why, not an
        exception. app/llm/gateway.py's health_check relies on this to
        aggregate across providers without one failure taking down the
        whole /health endpoint."""
        raise NotImplementedError
