"""Model creation tests for DocumentChunk (Sprint P10B1).

Same convention as tests/test_models.py: persist via the ORM, re-read in
a fresh query, and confirm defaults/relationships/cascade/uniqueness
work as declared - not just that the Python object can be constructed.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.document_chunk import DocumentChunk
from app.models.enums import ProductType
from app.models.product import Product


async def _make_asset(db_session: AsyncSession) -> Asset:
    product = Product(
        slug=f"product-{uuid.uuid4().hex[:10]}",
        title="Test Product",
        product_type=ProductType.PROMPT_PACK,
        price_cents=999,
        currency="USD",
    )
    asset = Asset(
        product=product,
        asset_type="source",
        filename="doc.txt",
        storage_key=f"assets/{uuid.uuid4()}/doc.txt",
        mime_type="text/plain",
        size_bytes=1024,
        checksum="deadbeef",
    )
    db_session.add(product)
    db_session.add(asset)
    await db_session.commit()
    await db_session.refresh(asset)
    return asset


async def test_document_chunk_creation_persists_all_fields(db_session: AsyncSession) -> None:
    asset = await _make_asset(db_session)
    chunk = DocumentChunk(
        asset_id=asset.id,
        chunk_index=0,
        content="hello world",
        content_hash="abc123",
        char_start=0,
        char_end=11,
    )
    db_session.add(chunk)
    await db_session.commit()
    await db_session.refresh(chunk)

    assert isinstance(chunk.id, uuid.UUID)
    assert chunk.asset_id == asset.id
    assert chunk.content == "hello world"
    assert chunk.content_hash == "abc123"
    assert chunk.char_start == 0
    assert chunk.char_end == 11
    assert chunk.created_at is not None
    assert chunk.updated_at is not None


async def test_document_chunk_asset_id_chunk_index_must_be_unique(
    db_session: AsyncSession,
) -> None:
    asset = await _make_asset(db_session)
    db_session.add(
        DocumentChunk(
            asset_id=asset.id,
            chunk_index=0,
            content="first",
            content_hash="hash-1",
            char_start=0,
            char_end=5,
        )
    )
    await db_session.commit()

    db_session.add(
        DocumentChunk(
            asset_id=asset.id,
            chunk_index=0,
            content="duplicate index",
            content_hash="hash-2",
            char_start=5,
            char_end=21,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_document_chunk_deleted_when_asset_deleted(db_session: AsyncSession) -> None:
    asset = await _make_asset(db_session)
    chunk = DocumentChunk(
        asset_id=asset.id,
        chunk_index=0,
        content="hello world",
        content_hash="abc123",
        char_start=0,
        char_end=11,
    )
    db_session.add(chunk)
    await db_session.commit()
    await db_session.refresh(chunk)
    chunk_id = chunk.id

    reloaded_asset = await db_session.get(Asset, asset.id)
    assert reloaded_asset is not None
    await db_session.delete(reloaded_asset)
    await db_session.commit()

    # .get() would return the stale identity-mapped object without
    # re-querying (db_session has expire_on_commit=False) - a select()
    # is required to observe the DB-level ON DELETE CASCADE, same as
    # test_models.py's own cascade-delete test.
    remaining = await db_session.scalar(
        select(DocumentChunk).where(DocumentChunk.id == chunk_id)
    )
    assert remaining is None
