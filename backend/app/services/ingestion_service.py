"""Ingestion service (Sprint P10B2): text extraction, chunking, and
embedding for a single Asset - ties app/rag/extraction.py and
app/rag/chunking.py to app/llm/gateway.py's LLMGateway.embed() and
app/models/document_chunk.py's DocumentChunk table, the same "service
layer ties a lower-level module to a table" pattern as
app/services/asset_service.py for app.storage.

Sprint P10B3 adds the HTTP endpoint deliberately deferred here
(POST/GET /assets/{asset_id}/ingest, app/api/v1/assets.py) - but it
enqueues a durable Job (app/jobs/service.py's enqueue_asset_ingestion)
rather than calling ingest_asset() directly from the request/response
cycle, same "don't block a request on a provider call" reasoning as
Sprint P8's agent/workflow run queueing. ingest_asset() itself is now
called from app/worker/dispatch.py instead. Still out of scope: no
re-ingestion/replace workflow - ingest_asset() is write-once per asset
(see AssetAlreadyIngestedError below); no retrieval/similarity search;
no OCR (see app/rag/extraction.py's docstring).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.gateway import LLMGateway
from app.llm.models import EmbeddingRequest
from app.models.asset import Asset
from app.models.document_chunk import DocumentChunk
from app.rag.chunking import chunk_text
from app.rag.exceptions import ExtractionError
from app.rag.extraction import SUPPORTED_CONTENT_TYPES, extract_text
from app.services import asset_service
from app.services.exceptions import (
    AssetAlreadyIngestedError,
    AssetNotIngestableError,
    AssetStorageOperationFailedError,
    EmptyExtractedTextError,
    IngestionEmbeddingMismatchError,
    IngestionExtractionFailedError,
)
from app.storage.base import StorageBackend
from app.storage.exceptions import StorageError


@dataclass(frozen=True)
class IngestionResult:
    asset_id: uuid.UUID
    chunk_count: int
    provider: str
    model: str
    total_input_tokens: int


async def check_ingestable(db: AsyncSession, asset_id: uuid.UUID) -> Asset:
    """The upfront, non-authoritative checks ingest_asset() itself
    performs before doing any storage/embedding work: the asset exists
    and isn't deleted, its content type has a text extractor, and it
    hasn't already been ingested. Factored out (Sprint P10B3) so
    app/jobs/service.py's enqueue_asset_ingestion can run the exact same
    checks synchronously at enqueue time - a bad asset_id, unsupported
    content type, or already-ingested asset fails the POST
    /assets/{id}/ingest request immediately (404/415/409) instead of
    only surfacing once a worker eventually dequeues the job.

    Not a substitute for the guarantees ingest_asset() and
    app/models/job.py's uq_jobs_active_asset_ingestion index provide:
    a race between this call and a concurrent enqueue/ingest is still
    possible - see AssetAlreadyIngestedError's and
    AssetIngestionAlreadyQueuedError's docstrings for how each race is
    actually closed at the database level. This function is only ever
    the fast path.
    """
    asset = await asset_service.get_asset(db, asset_id)  # AssetNotFoundError / AssetDeletedError

    content_type = asset.content_type or asset.mime_type
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise AssetNotIngestableError(asset_id, content_type)

    already_ingested = await db.scalar(
        select(DocumentChunk.id).where(DocumentChunk.asset_id == asset_id).limit(1)
    )
    if already_ingested is not None:
        raise AssetAlreadyIngestedError(asset_id)

    return asset


async def ingest_asset(
    db: AsyncSession,
    storage: StorageBackend,
    gateway: LLMGateway,
    *,
    asset_id: uuid.UUID,
    embedding_model: str,
    embedding_provider: str | None = None,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> IngestionResult:
    """Extracts text from `asset_id`'s stored content, splits it into
    deterministic chunks (app.rag.chunking.chunk_text), embeds all of
    them in a single batched LLMGateway.embed() call, and persists them
    as DocumentChunk rows in one commit.

    Every read (asset lookup, the already-ingested check, the storage
    read, extraction, chunking, the embedding call) happens before any
    write; the only database write in this function is the single
    db.add_all() + db.commit() at the end. A failure at any earlier step
    therefore leaves document_chunks completely untouched for this
    asset - there is no code path that persists a subset of an asset's
    chunks. See app/services/exceptions.py's "Ingestion domain errors"
    section for the exact failure -> exception mapping this function
    guarantees.

    Write-once: raises AssetAlreadyIngestedError if document_chunks rows
    already exist for this asset, whether caught by the upfront check
    below or by losing a concurrent ingest_asset() race at commit time
    (the (asset_id, chunk_index) unique constraint is the actual
    correctness guarantee; the upfront check is only a fast-path that
    avoids a wasted storage read and embedding call in the common,
    non-racing case).
    """
    asset = await check_ingestable(db, asset_id)
    content_type = asset.content_type or asset.mime_type

    try:
        content = await storage.open(asset.storage_key)
    except StorageError as exc:
        raise AssetStorageOperationFailedError("read") from exc

    try:
        text = extract_text(content, content_type)
    except ExtractionError as exc:
        raise IngestionExtractionFailedError(asset_id, str(exc)) from exc

    if not text.strip():
        raise EmptyExtractedTextError(asset_id)

    chunks = chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    embedding_request = EmbeddingRequest(
        request_id=str(uuid.uuid4()),
        model=embedding_model,
        input=tuple(chunk.content for chunk in chunks),
        provider=embedding_provider,
    )
    response = await gateway.embed(embedding_request)  # LLMError propagates unwrapped

    if len(response.embeddings) != len(chunks):
        raise IngestionEmbeddingMismatchError(asset_id, len(chunks), len(response.embeddings))

    chunk_rows = [
        DocumentChunk(
            asset_id=asset_id,
            chunk_index=chunk.index,
            content=chunk.content,
            content_hash=chunk.content_hash,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            embedding=list(vector),
            embedding_model=response.model,
            embedding_provider=response.provider,
        )
        for chunk, vector in zip(chunks, response.embeddings)
    ]

    db.add_all(chunk_rows)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AssetAlreadyIngestedError(asset_id) from exc

    return IngestionResult(
        asset_id=asset_id,
        chunk_count=len(chunk_rows),
        provider=response.provider,
        model=response.model,
        total_input_tokens=response.usage.input_tokens,
    )
