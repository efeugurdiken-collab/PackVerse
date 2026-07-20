"""Retrieval service (Sprint P10B4): embeds a query via
app/llm/gateway.py's LLMGateway.embed() (Sprint P10A) and ranks
app/models/document_chunk.py's DocumentChunk rows against it by pgvector
cosine distance - the same "service layer ties a lower-level module to
a table" pattern as app/services/ingestion_service.py for
app.rag/app.llm, and app/services/asset_service.py for app.storage.

Deliberately out of scope this sprint: no runtime RAG prompt injection,
no chat/agent integration, no answer generation, no reranking, no
hybrid keyword search, no worker/background retrieval (this is a
synchronous read, unlike ingestion - there is no provider write/state
change to protect from a request timeout), no OCR, no re-indexing, no
chunk update/delete workflows, and no HTTP endpoint - search() is a
plain async service function, callable directly (or from a script/REPL
against a running stack), same "no HTTP endpoint yet" starting point
Sprint P10B2's ingest_asset() had before P10B3 added one.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.gateway import LLMGateway
from app.llm.models import EmbeddingRequest
from app.models.asset import Asset
from app.models.document_chunk import DocumentChunk
from app.models.enums import AssetStatus
from app.rag.retrieval import ScoredChunk, cosine_distance_to_score
from app.services.exceptions import EmptyQueryError

DEFAULT_TOP_K = 10
# Tighter than app/services/asset_service.py's MAX_PAGE_SIZE (100) -
# each result carries full chunk content plus a per-row vector-distance
# computation, not just metadata.
MAX_TOP_K = 50


async def search(
    db: AsyncSession,
    gateway: LLMGateway,
    *,
    query: str,
    embedding_model: str,
    embedding_provider: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    asset_ids: Sequence[uuid.UUID] | None = None,
) -> list[ScoredChunk]:
    """Embeds `query` in a single LLMGateway.embed() call, then returns
    the `top_k` nearest DocumentChunk rows by cosine distance
    (`app.rag.retrieval.cosine_distance_to_score`), nearest first.

    Mandatory filtering, applied regardless of `asset_ids`:
    - `DocumentChunk.embedding_model == response.model` - the model the
      gateway actually resolved the request to (routing.py's alias
      resolution means this can differ from the `embedding_model`
      argument), NOT the raw argument. document_chunks.embedding_model
      was populated from this same resolved value at ingestion time
      (see ingestion_service.ingest_asset), so filtering on anything
      else would either compare differently-dimensioned vectors (a
      pgvector runtime error) or silently return zero rows whenever an
      alias was used. This is a correctness requirement, not a
      convenience default.
    - `Asset.status == AssetStatus.AVAILABLE` and `Asset.deleted_at IS
      NULL` - a soft-deleted or not-yet-available asset's chunks are
      never returned, mirroring asset_service.list_assets_for_product's
      own `Asset.status != AssetStatus.DELETED` filter.

    `asset_ids`, if given, additionally restricts results to those
    assets - `None` searches everything not otherwise excluded above;
    an explicitly empty sequence needs no special-casing, since
    `DocumentChunk.asset_id.in_(())` naturally matches nothing.

    `top_k` is clamped to `[1, MAX_TOP_K]`, never rejected - same
    convention as asset_service.list_assets_for_product's own
    limit/offset clamping, not a new raise-on-out-of-range pattern.

    Raises `EmptyQueryError` for a blank/whitespace-only `query`,
    before any embedding call. `LLMError` from `gateway.embed()`
    propagates unwrapped, same as it already does from
    `ingestion_service.ingest_asset` and `llm_service.py`. Never raises
    for "no results" - see this module's own tests for the exact empty-
    result cases (no chunks at all, no chunks under this embedding
    model, every candidate asset excluded, `asset_ids` matching
    nothing) - all return `[]`.
    """
    if not query.strip():
        raise EmptyQueryError()

    top_k = min(max(top_k, 1), MAX_TOP_K)

    embedding_request = EmbeddingRequest(
        request_id=str(uuid.uuid4()),
        model=embedding_model,
        input=(query,),
        provider=embedding_provider,
    )
    response = await gateway.embed(embedding_request)  # LLMError propagates unwrapped
    query_vector = list(response.embeddings[0])

    distance_expr = DocumentChunk.embedding.cosine_distance(query_vector)
    stmt = (
        select(DocumentChunk, distance_expr.label("distance"))
        .join(Asset, Asset.id == DocumentChunk.asset_id)
        .where(
            DocumentChunk.embedding_model == response.model,
            Asset.status == AssetStatus.AVAILABLE,
            Asset.deleted_at.is_(None),
        )
        .order_by(distance_expr)
        .limit(top_k)
    )
    if asset_ids is not None:
        stmt = stmt.where(DocumentChunk.asset_id.in_(asset_ids))

    rows = (await db.execute(stmt)).all()
    return [
        ScoredChunk(
            chunk_id=chunk.id,
            asset_id=chunk.asset_id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            distance=float(distance),
            score=cosine_distance_to_score(float(distance)),
        )
        for chunk, distance in rows
    ]
