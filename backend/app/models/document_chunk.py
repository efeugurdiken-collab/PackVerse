"""DocumentChunk model - a single deterministic text chunk extracted from
an Asset (Sprint P10B1: pgvector-capable Postgres + chunk storage).

Produced by app/rag/chunking.py's chunk_text(); this model only persists
the chunk (content, its position within the source text, and a
content_hash for later dedup/verification) - deliberately no embedding
column yet, see the migration
(alembic/versions/ad3f998eece8_*.py) docstring for why. Ingestion (the
code path that actually calls chunk_text() and writes rows here) is out
of scope for this sprint too; this model exists so the table can be
migrated and exercised by focused tests ahead of that.

chunk_index is 0-based and orders an asset's chunks; (asset_id,
chunk_index) is unique. Deleting an Asset cascades to its chunks - a
chunk has no independent existence apart from the asset it was
extracted from.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.asset import Asset


class DocumentChunk(Base, TimestampMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("asset_id", "chunk_index", name="uq_document_chunks_asset_id_chunk_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)

    asset: Mapped[Asset] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<DocumentChunk id={self.id} asset_id={self.asset_id} "
            f"chunk_index={self.chunk_index}>"
        )
