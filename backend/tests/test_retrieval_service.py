"""Tests for app.services.retrieval_service.search (Sprint P10B4): query
embedding -> pgvector cosine-distance ranking over document_chunks, and
every documented filter/failure/empty-result case (see that module's
docstring and app/services/exceptions.py's "Retrieval domain errors"
section).

Builds its own LLMGateway wrapping a small FakeProvider subclass
(_FixedVectorProvider) that returns a caller-specified fixed vector for
every embedding call, instead of FakeProvider's real hash-based
deterministic embedding - lets these tests construct document_chunks
with known, hand-picked vectors and assert on exact ranking/distance/
score without reverse-engineering FakeProvider's hash formula. Assets
are constructed via the shared make_asset fixture (Sprint P10B3);
chunks are constructed directly via the ORM (make_document_chunk has no
embedding/embedding_model params, so a local helper is used instead,
same "shared fixture doesn't fit, use a local helper" reasoning as
test_ingestion_service.py's own local _make_asset predating
make_asset).
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.exceptions import LLMAuthenticationError, LLMError
from app.llm.gateway import LLMGateway
from app.llm.models import EmbeddingRequest
from app.llm.providers.fake import FakeProvider
from app.models.document_chunk import DocumentChunk
from app.models.enums import AssetStatus
from app.services.exceptions import EmptyQueryError
from app.services.retrieval_service import MAX_TOP_K, search

_MODEL = "fake-embed-v1"


class _FixedVectorProvider(FakeProvider):
    """See module docstring."""

    def __init__(self, vector: tuple[float, ...], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._vector = vector

    async def embed(self, request: EmbeddingRequest):  # type: ignore[override]
        response = await super().embed(request)
        return replace(response, embeddings=tuple(self._vector for _ in request.input))


def _gateway(
    vector: tuple[float, ...] = (1.0, 0.0, 0.0),
    *,
    fail_with: LLMError | None = None,
    llm_model_aliases: str = "{}",
) -> LLMGateway:
    settings = Settings(
        jwt_secret_key="x" * 32, llm_default_provider="fake", llm_model_aliases=llm_model_aliases
    )
    return LLMGateway({"fake": _FixedVectorProvider(vector, fail_with=fail_with)}, settings)


async def _make_chunk(
    db_session: AsyncSession,
    *,
    asset_id: uuid.UUID,
    content: str,
    embedding: tuple[float, ...],
    embedding_model: str = _MODEL,
    embedding_provider: str = "fake",
    chunk_index: int = 0,
) -> DocumentChunk:
    chunk = DocumentChunk(
        asset_id=asset_id,
        chunk_index=chunk_index,
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        char_start=0,
        char_end=len(content),
        embedding=list(embedding),
        embedding_model=embedding_model,
        embedding_provider=embedding_provider,
    )
    db_session.add(chunk)
    await db_session.commit()
    await db_session.refresh(chunk)
    return chunk


# --- ranking order / distance / score -------------------------------------


async def test_search_ranks_nearest_first_with_consistent_distance_and_score(
    db_session, make_asset
) -> None:
    asset = await make_asset()
    # Inserted out of rank order on purpose - the query itself must sort.
    orthogonal = await _make_chunk(
        db_session, asset_id=asset.id, content="orthogonal", embedding=(0.0, 1.0, 0.0),
        chunk_index=0,
    )
    identical = await _make_chunk(
        db_session, asset_id=asset.id, content="identical", embedding=(1.0, 0.0, 0.0),
        chunk_index=1,
    )
    diagonal = await _make_chunk(
        db_session, asset_id=asset.id, content="diagonal", embedding=(0.7071, 0.7071, 0.0),
        chunk_index=2,
    )

    results = await search(
        db_session, _gateway(), query="q", embedding_model=_MODEL, top_k=10
    )

    assert [r.chunk_id for r in results] == [identical.id, diagonal.id, orthogonal.id]
    assert results[0].distance == pytest.approx(0.0, abs=1e-6)
    assert results[0].score == pytest.approx(1.0, abs=1e-6)
    assert results[1].score == pytest.approx(1.0 - results[1].distance)
    assert results[-1].distance == pytest.approx(1.0, abs=1e-6)
    assert results[-1].score == pytest.approx(0.0, abs=1e-6)


async def test_search_returns_chunk_content_and_position_fields(db_session, make_asset) -> None:
    asset = await make_asset()
    chunk = await _make_chunk(
        db_session, asset_id=asset.id, content="hello world", embedding=(1.0, 0.0, 0.0),
        chunk_index=3,
    )

    results = await search(db_session, _gateway(), query="q", embedding_model=_MODEL)

    assert len(results) == 1
    assert results[0].asset_id == asset.id
    assert results[0].chunk_index == 3
    assert results[0].content == "hello world"
    assert results[0].char_start == chunk.char_start
    assert results[0].char_end == chunk.char_end


# --- top_k -----------------------------------------------------------------


async def test_search_top_k_limits_result_count(db_session, make_asset) -> None:
    asset = await make_asset()
    for i, vec in enumerate([(1.0, 0.0, 0.0), (0.9, 0.1, 0.0), (0.0, 1.0, 0.0)]):
        await _make_chunk(db_session, asset_id=asset.id, content=f"c{i}", embedding=vec, chunk_index=i)

    results = await search(db_session, _gateway(), query="q", embedding_model=_MODEL, top_k=2)
    assert len(results) == 2


async def test_search_top_k_is_clamped_to_at_least_one(db_session, make_asset) -> None:
    asset = await make_asset()
    await _make_chunk(db_session, asset_id=asset.id, content="only", embedding=(1.0, 0.0, 0.0))

    results = await search(db_session, _gateway(), query="q", embedding_model=_MODEL, top_k=0)
    assert len(results) == 1


async def test_search_top_k_is_clamped_to_max_top_k(db_session, make_asset) -> None:
    asset = await make_asset()
    await _make_chunk(db_session, asset_id=asset.id, content="only", embedding=(1.0, 0.0, 0.0))

    # No error, no attempt to actually return MAX_TOP_K+ rows (only one
    # chunk exists) - proves the clamp doesn't reject an oversized
    # request the way an out-of-range raise would.
    results = await search(
        db_session, _gateway(), query="q", embedding_model=_MODEL, top_k=MAX_TOP_K + 1000
    )
    assert len(results) == 1


# --- asset_ids filtering -----------------------------------------------------


async def test_search_asset_ids_filters_to_named_assets(db_session, make_asset) -> None:
    included = await make_asset()
    excluded = await make_asset()
    included_chunk = await _make_chunk(
        db_session, asset_id=included.id, content="in", embedding=(1.0, 0.0, 0.0)
    )
    await _make_chunk(db_session, asset_id=excluded.id, content="out", embedding=(1.0, 0.0, 0.0))

    results = await search(
        db_session, _gateway(), query="q", embedding_model=_MODEL, asset_ids=[included.id]
    )

    assert [r.chunk_id for r in results] == [included_chunk.id]


async def test_search_no_asset_ids_filter_searches_everything(db_session, make_asset) -> None:
    first = await make_asset()
    second = await make_asset()
    await _make_chunk(db_session, asset_id=first.id, content="a", embedding=(1.0, 0.0, 0.0))
    await _make_chunk(db_session, asset_id=second.id, content="b", embedding=(0.9, 0.1, 0.0))

    results = await search(db_session, _gateway(), query="q", embedding_model=_MODEL)
    assert len(results) == 2


async def test_search_empty_asset_ids_list_matches_nothing(db_session, make_asset) -> None:
    asset = await make_asset()
    await _make_chunk(db_session, asset_id=asset.id, content="a", embedding=(1.0, 0.0, 0.0))

    results = await search(db_session, _gateway(), query="q", embedding_model=_MODEL, asset_ids=[])
    assert results == []


# --- asset availability / soft-delete isolation -----------------------------


async def test_search_excludes_chunks_of_pending_asset(db_session, make_asset) -> None:
    asset = await make_asset(status=AssetStatus.PENDING)
    await _make_chunk(db_session, asset_id=asset.id, content="a", embedding=(1.0, 0.0, 0.0))

    results = await search(db_session, _gateway(), query="q", embedding_model=_MODEL)
    assert results == []


async def test_search_excludes_chunks_of_failed_asset(db_session, make_asset) -> None:
    asset = await make_asset(status=AssetStatus.FAILED)
    await _make_chunk(db_session, asset_id=asset.id, content="a", embedding=(1.0, 0.0, 0.0))

    results = await search(db_session, _gateway(), query="q", embedding_model=_MODEL)
    assert results == []


async def test_search_excludes_chunks_of_soft_deleted_asset(db_session, make_asset) -> None:
    asset = await make_asset(status=AssetStatus.AVAILABLE)
    await _make_chunk(db_session, asset_id=asset.id, content="a", embedding=(1.0, 0.0, 0.0))
    asset.deleted_at = datetime.now(timezone.utc)
    db_session.add(asset)
    await db_session.commit()

    results = await search(db_session, _gateway(), query="q", embedding_model=_MODEL)
    assert results == []


async def test_search_excludes_soft_deleted_asset_even_when_named_in_asset_ids(
    db_session, make_asset
) -> None:
    """asset_ids is a caller-supplied filter, not a bypass of the
    mandatory availability check - explicitly naming a deleted asset
    must not resurrect its chunks."""
    asset = await make_asset(status=AssetStatus.AVAILABLE)
    await _make_chunk(db_session, asset_id=asset.id, content="a", embedding=(1.0, 0.0, 0.0))
    asset.deleted_at = datetime.now(timezone.utc)
    db_session.add(asset)
    await db_session.commit()

    results = await search(
        db_session, _gateway(), query="q", embedding_model=_MODEL, asset_ids=[asset.id]
    )
    assert results == []


# --- embedding_model isolation (mandatory, not the optional asset filter) --


async def test_search_excludes_chunks_embedded_under_a_different_model(
    db_session, make_asset
) -> None:
    """A same-vector (distance 0) chunk under a different embedding_model
    must never outrank - or even appear alongside - matches under the
    requested model. Proves the model filter is load-bearing, not
    cosmetic: without it this chunk would rank first."""
    asset = await make_asset()
    other_model_chunk = await _make_chunk(
        db_session, asset_id=asset.id, content="wrong model", embedding=(1.0, 0.0, 0.0),
        embedding_model="a-different-model",
    )
    right_model_chunk = await _make_chunk(
        db_session, asset_id=asset.id, content="right model", embedding=(0.5, 0.5, 0.0),
        embedding_model=_MODEL, chunk_index=1,
    )

    results = await search(db_session, _gateway(), query="q", embedding_model=_MODEL)

    assert [r.chunk_id for r in results] == [right_model_chunk.id]
    assert other_model_chunk.id not in [r.chunk_id for r in results]


async def test_search_filters_by_the_resolved_model_not_the_requested_alias(
    db_session, make_asset
) -> None:
    """embedding_model="my-alias" resolves (via LLMGateway's real
    routing, not a shortcut) to "resolved-model-name" - the chunk stored
    under the resolved name must still be found, proving search()
    filters on response.model (what the gateway actually used), not the
    raw argument the caller passed in."""
    asset = await make_asset()
    chunk = await _make_chunk(
        db_session, asset_id=asset.id, content="a", embedding=(1.0, 0.0, 0.0),
        embedding_model="resolved-model-name",
    )
    gateway = _gateway(
        llm_model_aliases='{"fake": {"my-alias": "resolved-model-name"}}'
    )

    results = await search(db_session, gateway, query="q", embedding_model="my-alias")

    assert [r.chunk_id for r in results] == [chunk.id]


# --- empty results -----------------------------------------------------------


async def test_search_empty_corpus_returns_empty_list(db_session) -> None:
    results = await search(db_session, _gateway(), query="q", embedding_model=_MODEL)
    assert results == []


# --- validation / failure behavior ------------------------------------------


async def test_search_blank_query_raises_without_calling_the_gateway() -> None:
    # fail_with proves the gateway was never reached - if embed() had
    # been called this would raise LLMAuthenticationError instead.
    gateway = _gateway(fail_with=LLMAuthenticationError("fake"))
    with pytest.raises(EmptyQueryError):
        await search(None, gateway, query="   ", embedding_model=_MODEL)  # type: ignore[arg-type]


async def test_search_llm_error_propagates_unwrapped(db_session) -> None:
    gateway = _gateway(fail_with=LLMAuthenticationError("fake"))
    with pytest.raises(LLMAuthenticationError):
        await search(db_session, gateway, query="q", embedding_model=_MODEL)
