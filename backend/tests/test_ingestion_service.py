"""Tests for app.services.ingestion_service.ingest_asset (Sprint P10B2):
text extraction -> chunking -> embedding -> DocumentChunk persistence,
and every documented failure mode (see
app/services/ingestion_service.py's docstring and
app/services/exceptions.py's "Ingestion domain errors" section).

Builds its own LLMGateway wrapping FakeProvider, same pattern as
tests/test_llm_gateway.py - no network, no HTTP mocking. Assets are
constructed directly (Product + Asset via the ORM, same convention as
tests/test_document_chunk_models.py) with real bytes written through the
storage_backend fixture, so ingest_asset's own storage.open() call reads
genuine content rather than a mock.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.exceptions import LLMAuthenticationError, LLMError
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeProvider
from app.models.asset import Asset
from app.models.document_chunk import DocumentChunk
from app.models.enums import AssetStatus, ProductType
from app.models.product import Product
from app.rag.chunking import chunk_text
from app.services.exceptions import (
    AssetAlreadyIngestedError,
    AssetDeletedError,
    AssetNotFoundError,
    AssetNotIngestableError,
    EmptyExtractedTextError,
    IngestionExtractionFailedError,
)
from app.services.ingestion_service import ingest_asset
from app.storage.base import StorageBackend

_EMBEDDING_MODEL = "fake-embed-v1"


def _gateway(*, fail_with: LLMError | None = None) -> LLMGateway:
    settings = Settings(jwt_secret_key="x" * 32, llm_default_provider="fake")
    return LLMGateway({"fake": FakeProvider(fail_with=fail_with)}, settings)


async def _make_asset(
    db_session: AsyncSession,
    storage_backend: StorageBackend,
    *,
    content: bytes,
    content_type: str,
) -> Asset:
    product = Product(
        slug=f"product-{uuid.uuid4().hex[:10]}",
        title="Test Product",
        product_type=ProductType.PROMPT_PACK,
        price_cents=999,
        currency="USD",
    )
    storage_key = f"assets/{uuid.uuid4()}/doc"
    await storage_backend.store(storage_key, content, content_type=content_type)
    asset = Asset(
        product=product,
        asset_type="source",
        filename="doc",
        storage_key=storage_key,
        mime_type=content_type,
        content_type=content_type,
        size_bytes=len(content),
        checksum="deadbeef",
    )
    db_session.add(product)
    db_session.add(asset)
    await db_session.commit()
    await db_session.refresh(asset)
    return asset


def _build_minimal_pdf(text: str) -> bytes:
    """Hand-built, byte-correct single-page PDF with a real text content
    stream - same technique as tests/test_extraction.py's
    _build_minimal_pdf, kept as an independent copy here rather than a
    cross-test-file import so this file's fixtures aren't coupled to
    that one's internals."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    content_obj = (
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> "
            b"/MediaBox [0 0 612 792] /Contents 4 0 R >>"
        ),
        content_obj,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(buf)
    total_objs = len(objects) + 1
    buf += f"xref\n0 {total_objs}\n".encode()
    buf += b"0000000000 65535 f \n"
    for off in offsets:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()
    return bytes(buf)


async def _chunk_rows(db_session: AsyncSession, asset_id: uuid.UUID) -> list[DocumentChunk]:
    result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.asset_id == asset_id).order_by(
            DocumentChunk.chunk_index
        )
    )
    return list(result.scalars().all())


# --- happy path ---


async def test_ingest_asset_text_plain_happy_path(
    db_session: AsyncSession, storage_backend: StorageBackend
) -> None:
    text = "one two three four five " * 20
    asset = await _make_asset(
        db_session, storage_backend, content=text.encode("utf-8"), content_type="text/plain"
    )

    result = await ingest_asset(
        db_session,
        storage_backend,
        _gateway(),
        asset_id=asset.id,
        embedding_model=_EMBEDDING_MODEL,
        embedding_provider="fake",
        chunk_size=50,
        chunk_overlap=10,
    )

    expected_chunks = chunk_text(text, chunk_size=50, chunk_overlap=10)
    assert result.chunk_count == len(expected_chunks)
    assert result.provider == "fake"
    assert result.model == _EMBEDDING_MODEL
    assert result.total_input_tokens > 0

    rows = await _chunk_rows(db_session, asset.id)
    assert len(rows) == len(expected_chunks)
    for row, expected in zip(rows, expected_chunks):
        assert row.content == expected.content
        assert row.content_hash == expected.content_hash
        assert row.char_start == expected.char_start
        assert row.char_end == expected.char_end
        assert row.embedding is not None
        assert len(row.embedding) > 0
        assert row.embedding_model == _EMBEDDING_MODEL
        assert row.embedding_provider == "fake"


async def test_ingest_asset_pdf_happy_path(
    db_session: AsyncSession, storage_backend: StorageBackend
) -> None:
    pdf_bytes = _build_minimal_pdf("Hello ingestion world")
    asset = await _make_asset(
        db_session, storage_backend, content=pdf_bytes, content_type="application/pdf"
    )

    result = await ingest_asset(
        db_session,
        storage_backend,
        _gateway(),
        asset_id=asset.id,
        embedding_model=_EMBEDDING_MODEL,
    )

    assert result.chunk_count >= 1
    rows = await _chunk_rows(db_session, asset.id)
    assert any("Hello ingestion world" in row.content for row in rows)
    assert all(row.embedding is not None for row in rows)


# --- failure modes ---


async def test_ingest_asset_missing_asset_raises(
    db_session: AsyncSession, storage_backend: StorageBackend
) -> None:
    with pytest.raises(AssetNotFoundError):
        await ingest_asset(
            db_session,
            storage_backend,
            _gateway(),
            asset_id=uuid.uuid4(),
            embedding_model=_EMBEDDING_MODEL,
        )


async def test_ingest_asset_deleted_asset_raises(
    db_session: AsyncSession, storage_backend: StorageBackend
) -> None:
    asset = await _make_asset(
        db_session, storage_backend, content=b"hello", content_type="text/plain"
    )
    asset.status = AssetStatus.DELETED
    asset.deleted_at = datetime.now(timezone.utc)
    db_session.add(asset)
    await db_session.commit()

    with pytest.raises(AssetDeletedError):
        await ingest_asset(
            db_session,
            storage_backend,
            _gateway(),
            asset_id=asset.id,
            embedding_model=_EMBEDDING_MODEL,
        )


async def test_ingest_asset_unsupported_content_type_raises(
    db_session: AsyncSession, storage_backend: StorageBackend
) -> None:
    asset = await _make_asset(
        db_session, storage_backend, content=b"\x89PNG\r\n", content_type="image/png"
    )
    with pytest.raises(AssetNotIngestableError):
        await ingest_asset(
            db_session,
            storage_backend,
            _gateway(),
            asset_id=asset.id,
            embedding_model=_EMBEDDING_MODEL,
        )
    assert await _chunk_rows(db_session, asset.id) == []


async def test_ingest_asset_invalid_utf8_raises_extraction_error(
    db_session: AsyncSession, storage_backend: StorageBackend
) -> None:
    asset = await _make_asset(
        db_session, storage_backend, content=b"\xff\xfe not valid utf-8", content_type="text/plain"
    )
    with pytest.raises(IngestionExtractionFailedError):
        await ingest_asset(
            db_session,
            storage_backend,
            _gateway(),
            asset_id=asset.id,
            embedding_model=_EMBEDDING_MODEL,
        )
    assert await _chunk_rows(db_session, asset.id) == []


async def test_ingest_asset_corrupt_pdf_raises_extraction_error(
    db_session: AsyncSession, storage_backend: StorageBackend
) -> None:
    asset = await _make_asset(
        db_session,
        storage_backend,
        content=b"%PDF-1.4\nnot a well-formed pdf body at all",
        content_type="application/pdf",
    )
    with pytest.raises(IngestionExtractionFailedError):
        await ingest_asset(
            db_session,
            storage_backend,
            _gateway(),
            asset_id=asset.id,
            embedding_model=_EMBEDDING_MODEL,
        )
    assert await _chunk_rows(db_session, asset.id) == []


async def test_ingest_asset_empty_text_raises(
    db_session: AsyncSession, storage_backend: StorageBackend
) -> None:
    asset = await _make_asset(
        db_session, storage_backend, content=b"   \n\t  ", content_type="text/plain"
    )
    with pytest.raises(EmptyExtractedTextError):
        await ingest_asset(
            db_session,
            storage_backend,
            _gateway(),
            asset_id=asset.id,
            embedding_model=_EMBEDDING_MODEL,
        )
    assert await _chunk_rows(db_session, asset.id) == []


async def test_ingest_asset_embedding_failure_propagates_and_writes_nothing(
    db_session: AsyncSession, storage_backend: StorageBackend
) -> None:
    asset = await _make_asset(
        db_session,
        storage_backend,
        content=b"some real text content to embed",
        content_type="text/plain",
    )
    with pytest.raises(LLMAuthenticationError):
        await ingest_asset(
            db_session,
            storage_backend,
            _gateway(fail_with=LLMAuthenticationError("fake")),
            asset_id=asset.id,
            embedding_model=_EMBEDDING_MODEL,
        )
    assert await _chunk_rows(db_session, asset.id) == []


async def test_ingest_asset_already_ingested_raises_and_leaves_existing_chunks(
    db_session: AsyncSession, storage_backend: StorageBackend
) -> None:
    asset = await _make_asset(
        db_session,
        storage_backend,
        content=b"some real text content to embed",
        content_type="text/plain",
    )
    first = await ingest_asset(
        db_session,
        storage_backend,
        _gateway(),
        asset_id=asset.id,
        embedding_model=_EMBEDDING_MODEL,
    )
    assert first.chunk_count >= 1

    with pytest.raises(AssetAlreadyIngestedError):
        await ingest_asset(
            db_session,
            storage_backend,
            _gateway(),
            asset_id=asset.id,
            embedding_model=_EMBEDDING_MODEL,
        )

    rows = await _chunk_rows(db_session, asset.id)
    assert len(rows) == first.chunk_count


async def test_ingest_asset_chunk_params_pass_through(
    db_session: AsyncSession, storage_backend: StorageBackend
) -> None:
    text = "word " * 100
    asset = await _make_asset(
        db_session, storage_backend, content=text.encode("utf-8"), content_type="text/plain"
    )

    result = await ingest_asset(
        db_session,
        storage_backend,
        _gateway(),
        asset_id=asset.id,
        embedding_model=_EMBEDDING_MODEL,
        chunk_size=30,
        chunk_overlap=5,
    )
    assert result.chunk_count == len(chunk_text(text, chunk_size=30, chunk_overlap=5))
