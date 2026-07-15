"""extend assets table for storage layer (Sprint P4)

Revision ID: ae14cc314d2f
Revises: 1f20f57819a3
Create Date: 2026-07-15 00:00:00.000000

Hand-written, same reason as every prior migration in this project: no
PostgreSQL instance or network access available in the sandbox this was
written in. Written directly from app/models/asset.py; verify against a
real database before merging (see docs/P1_LOCAL_VERIFICATION.md).

Only ALTERs the existing assets table - does not touch products, jobs,
agent_definitions, workflow_definitions, or users, and does not edit any
prior migration file.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ae14cc314d2f"
down_revision: Union[str, None] = "1f20f57819a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets", sa.Column("original_filename", sa.String(length=512), nullable=True)
    )
    op.add_column("assets", sa.Column("content_type", sa.String(length=255), nullable=True))
    op.add_column("assets", sa.Column("etag", sa.String(length=255), nullable=True))
    op.add_column(
        "assets",
        sa.Column(
            "storage_backend",
            sa.Enum("local", "s3", name="storage_provider", native_enum=False, length=16),
            nullable=False,
            server_default="local",
        ),
    )
    op.add_column(
        "assets",
        sa.Column(
            "status",
            sa.Enum(
                "pending", "available", "failed", "deleted",
                name="asset_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
            # Pre-P4 rows (if any) represent already-completed uploads
            # from the P2 API, so "available" is the correct backfill
            # value here even though new Python-side inserts default to
            # "pending" (see app/models/asset.py) until the upload
            # endpoint confirms the storage write actually succeeded.
            server_default="available",
        ),
    )
    op.add_column(
        "assets",
        sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("assets", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key(
        "fk_assets_uploaded_by_user_id_users",
        "assets",
        "users",
        ["uploaded_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_assets_status", "assets", ["status"], unique=False)
    op.create_index(
        "ix_assets_uploaded_by_user_id", "assets", ["uploaded_by_user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_assets_uploaded_by_user_id", table_name="assets")
    op.drop_index("ix_assets_status", table_name="assets")
    op.drop_constraint("fk_assets_uploaded_by_user_id_users", "assets", type_="foreignkey")

    op.drop_column("assets", "deleted_at")
    op.drop_column("assets", "uploaded_by_user_id")
    op.drop_column("assets", "status")
    op.drop_column("assets", "storage_backend")
    op.drop_column("assets", "etag")
    op.drop_column("assets", "content_type")
    op.drop_column("assets", "original_filename")
