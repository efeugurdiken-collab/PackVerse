"""create workflow_runs and workflow_step_runs tables (Sprint P7:
Workflow Orchestration)

Revision ID: d4e6b9a3f1c7
Revises: a1c8f7d2b3e9
Create Date: 2026-07-18 00:00:00.000000

Hand-written, same reason as every prior migration in this project: no
PostgreSQL instance or network access available in the sandbox this was
written in. Written directly from app/models/workflow_run.py and
app/models/workflow_step_run.py; verify against a real database before
merging (see docs/P1_LOCAL_VERIFICATION.md).

Only adds the two new tables - does not touch products, assets, jobs,
agent_definitions, workflow_definitions, users, llm_requests, or
agent_runs, and does not edit any prior migration file.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e6b9a3f1c7"
down_revision: Union[str, None] = "a1c8f7d2b3e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "queued", "running", "completed", "failed", "cancelled",
                name="workflow_run_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
            server_default="queued",
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_workflow_runs"),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflow_definitions.id"],
            name="fk_workflow_runs_workflow_id_workflow_definitions",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_workflow_runs_created_by_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_workflow_runs_workflow_id", "workflow_runs", ["workflow_id"], unique=False)
    op.create_index(
        "ix_workflow_runs_created_by_user_id", "workflow_runs", ["created_by_user_id"], unique=False
    )
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"], unique=False)

    op.create_table(
        "workflow_step_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", sa.String(length=128), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_snapshot", sa.Text(), nullable=True),
        sa.Column("output_text", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "running", "completed", "failed", "cancelled", "skipped",
                name="workflow_step_run_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
            server_default="pending",
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_workflow_step_runs"),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            name="fk_workflow_step_runs_workflow_run_id_workflow_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_definitions.id"],
            name="fk_workflow_step_runs_agent_id_agent_definitions",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_workflow_step_runs_agent_run_id_agent_runs",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_workflow_step_runs_workflow_run_id",
        "workflow_step_runs",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_step_runs_agent_id", "workflow_step_runs", ["agent_id"], unique=False
    )
    op.create_index(
        "ix_workflow_step_runs_agent_run_id", "workflow_step_runs", ["agent_run_id"], unique=False
    )
    op.create_index(
        "ix_workflow_step_runs_status", "workflow_step_runs", ["status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_step_runs_status", table_name="workflow_step_runs")
    op.drop_index("ix_workflow_step_runs_agent_run_id", table_name="workflow_step_runs")
    op.drop_index("ix_workflow_step_runs_agent_id", table_name="workflow_step_runs")
    op.drop_index("ix_workflow_step_runs_workflow_run_id", table_name="workflow_step_runs")
    op.drop_table("workflow_step_runs")

    op.drop_index("ix_workflow_runs_status", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_created_by_user_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_workflow_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")
