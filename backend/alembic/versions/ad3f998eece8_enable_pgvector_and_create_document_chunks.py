"""enable pgvector extension and create document_chunks table (Sprint P10B1)

Revision ID: ad3f998eece8
Revises: d657afc740be
Create Date: 2026-07-18 00:00:00.000000

Infra step for the RAG track (roadmap item 10): enables the `vector`
Postgres extension (requires the pgvector/pgvector:pg16 image - see
docker-compose.yml) and creates document_chunks, which stores the
deterministic text chunks produced by app/rag/chunking.py for a given
Asset. Deliberately no embedding/vector column yet - P10A's provider-
agnostic embedding foundation (app/llm/models.py's EmbeddingRequest/
EmbeddingResponse) doesn't fix a dimension, and embedding calls are out
of scope for this sprint; a later sprint adds that column once a
provider/dimension is chosen. Nothing else in this migration - no
ingestion, retrieval, or worker changes ride along with it.

chunk_index is 0-based, ordering an asset's chunks; content_hash is the
sha256 hex digest of `content`, as returned by app/rag/chunking.py's
Chunk.content_hash, kept as a column (not recomputed) so a later
retrieval/dedup step never needs to re-hash stored text. asset_id
cascades on Asset delete - chunks have no independent existence apart
from the asset they were extracted from.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ad3f998eece8"
down_revision: Union[str, None] = "d657afc740be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name="fk_document_chunks_asset_id_assets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
    )
    op.create_index(
        "ix_document_chunks_asset_id", "document_chunks", ["asset_id"], unique=False
    )
    op.create_unique_constraint(
        "uq_document_chunks_asset_id_chunk_index",
        "document_chunks",
        ["asset_id", "chunk_index"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_document_chunks_asset_id_chunk_index", "document_chunks", type_="unique"
    )
    op.drop_index("ix_document_chunks_asset_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.execute("DROP EXTENSION IF EXISTS vector")
