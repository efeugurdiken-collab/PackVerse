"""create agent_runs table (Sprint P6: AI Runtime)

Revision ID: a1c8f7d2b3e9
Revises: 7c19e4b8a2d6
Create Date: 2026-07-17 00:00:00.000000

Hand-written, same reason as every prior migration in this project: no
PostgreSQL instance or network access available in the sandbox this was
written in. Written directly from app/models/agent_run.py; verify
against a real database before merging (see docs/P1_LOCAL_VERIFICATION.md).

Only adds the new agent_runs table - does not touch products, assets,
jobs, agent_definitions, workflow_definitions, users, or llm_requests,
and does not edit any prior migration file. See app/models/agent_run.py's
module docstring for why output_text is stored here even though
llm_requests (P5) deliberately excludes generated content - and for why
raw user_input/context are NOT stored here either.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c8f7d2b3e9"
down_revision: Union[str, None] = "7c19e4b8a2d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "queued", "running", "completed", "failed", "cancelled",
                name="agent_run_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("llm_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("output_text", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agent_definitions.id"], name="fk_agent_runs_agent_id_agent_definitions"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_agent_runs_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["llm_request_id"],
            ["llm_requests.id"],
            name="fk_agent_runs_llm_request_id_llm_requests",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_agent_runs_agent_id", "agent_runs", ["agent_id"], unique=False)
    op.create_index(
        "ix_agent_runs_created_by_user_id", "agent_runs", ["created_by_user_id"], unique=False
    )
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"], unique=False)
    op.create_index(
        "ix_agent_runs_llm_request_id", "agent_runs", ["llm_request_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_llm_request_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_created_by_user_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_agent_id", table_name="agent_runs")
    op.drop_table("agent_runs")
