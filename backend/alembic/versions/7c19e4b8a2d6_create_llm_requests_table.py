"""create llm_requests table (Sprint P5: LLM Gateway)

Revision ID: 7c19e4b8a2d6
Revises: ae14cc314d2f
Create Date: 2026-07-16 00:00:00.000000

Hand-written, same reason as every prior migration in this project: no
PostgreSQL instance or network access available in the sandbox this was
written in. Written directly from app/models/llm_request.py; verify
against a real database before merging (see docs/P1_LOCAL_VERIFICATION.md).

Only adds the new llm_requests table - does not touch products, assets,
jobs, agent_definitions, workflow_definitions, or users, and does not
edit any prior migration file. No prompt or generated-content column
exists here by design - see the model's module docstring.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c19e4b8a2d6"
down_revision: Union[str, None] = "ae14cc314d2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "succeeded", "failed",
                name="llm_request_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "request_metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "response_metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_llm_requests"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_llm_requests_user_id_users", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_llm_requests_user_id", "llm_requests", ["user_id"], unique=False)
    op.create_index("ix_llm_requests_provider", "llm_requests", ["provider"], unique=False)
    op.create_index("ix_llm_requests_status", "llm_requests", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_llm_requests_status", table_name="llm_requests")
    op.drop_index("ix_llm_requests_provider", table_name="llm_requests")
    op.drop_index("ix_llm_requests_user_id", table_name="llm_requests")
    op.drop_table("llm_requests")
