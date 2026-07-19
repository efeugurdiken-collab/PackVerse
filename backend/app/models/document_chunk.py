"""DocumentChunk model - a single deterministic text chunk extracted from
an Asset (Sprint P10B1: pgvector-capable Postgres + chunk storage;
Sprint P10B2: embedding persistence + ingestion).

Produced by app/rag/chunking.py's chunk_text() and persisted by
app/services/ingestion_service.py's ingest_asset(), together with the
embedding vector app/llm/gateway.py's LLMGateway.embed() returned for
that chunk's content.

embedding is a pgvector column with no fixed dimension (Vector(dim=None),
see the migration alembic/versions/*_add_embedding_columns_to_document_chunks.py's
docstring for why) - different embedding models produce different-length
vectors, and this table stores them all without picking one dimension.
embedding_model/embedding_provider record which model/provider actually
produced the vector (the provider/model the LLM Gateway resolved, not
necessarily what the caller requested - see EmbeddingResponse), so a
future retrieval step can group by model before doing anything
dimension-sensitive like a similarity comparison. All three columns are
nullable only because SQLAlchemy/Alembic require new non-PK columns on
an existing table to have a default or be nullable; ingest_asset() always
sets all three together, in the same row-construction step - there is no
supported path that leaves a DocumentChunk row with some but not all of
them set.

chunk_index is 0-based and orders an asset's chunks; (asset_id,
chunk_index) is unique. Deleting an Asset cascades to its chunks - a
chunk has no independent existence apart from the asset it was
extracted from.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
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

    # --- Sprint P10B2 ---
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)

    asset: Mapped[Asset] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<DocumentChunk id={self.id} asset_id={self.asset_id} "
            f"chunk_index={self.chunk_index}>"
        )
