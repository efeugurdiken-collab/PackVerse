"""add embedding columns to document_chunks (Sprint P10B2)

Revision ID: e4ba9bdd172a
Revises: ad3f998eece8
Create Date: 2026-07-19 00:00:00.000000

Adds the embedding column P10B1's migration deliberately deferred (see
that migration's docstring): embedding, a pgvector column with no fixed
dimension (`vector`, not `vector(N)`) since different embedding models
produce different-length vectors and this table doesn't pick one, plus
embedding_model/embedding_provider to record which model/provider
actually produced a given row's vector. All three are nullable - see
app/models/document_chunk.py's docstring for why that's not a data-
integrity gap. No backfill: no ingestion path existed before this
sprint's app/services/ingestion_service.py, so there are no pre-existing
document_chunks rows to update. Nothing else in this migration - no new
table, no change to any other table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "e4ba9bdd172a"
down_revision: Union[str, None] = "ad3f998eece8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("embedding", Vector(), nullable=True))
    op.add_column(
        "document_chunks", sa.Column("embedding_model", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "document_chunks", sa.Column("embedding_provider", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "embedding_provider")
    op.drop_column("document_chunks", "embedding_model")
    op.drop_column("document_chunks", "embedding")
