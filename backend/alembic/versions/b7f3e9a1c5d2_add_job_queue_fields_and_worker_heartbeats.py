"""add job queue fields and worker_heartbeats table (Sprint P8:
Asynchronous Job Execution)

Revision ID: b7f3e9a1c5d2
Revises: d4e6b9a3f1c7
Create Date: 2026-07-21 00:00:00.000000

Hand-written, same reason as every prior migration in this project: no
PostgreSQL instance or network access available in the sandbox this was
written in. Written directly from app/models/job.py and
app/models/worker_heartbeat.py; verify against a real database before
merging (see docs/P1_LOCAL_VERIFICATION.md).

Adds the durable-queue columns to the existing `jobs` table (a P2
placeholder never used by any real code path until this sprint - see
app/models/job.py's module docstring) plus a new `worker_heartbeats`
table. Does not touch products, assets, agent_definitions,
workflow_definitions, users, llm_requests, agent_runs, workflow_runs,
workflow_step_runs, and does not edit any prior migration file.

The `job_status` enum's Python-level values changed in this sprint
(pending/succeeded -> queued/completed, plus a new retrying value) but
`native_enum=False` means the column is a plain VARCHAR with app-level
validation, not a Postgres-native ENUM or a CHECK constraint - so this
migration only needs to update the column's server_default string, not
run any ALTER TYPE.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7f3e9a1c5d2"
down_revision: Union[str, None] = "d4e6b9a3f1c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("target_run_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("jobs", sa.Column("error_code", sa.String(length=128), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "jobs",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("worker_id", sa.String(length=128), nullable=True))
    op.add_column(
        "jobs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.alter_column("jobs", "status", server_default="queued")

    op.create_index("ix_jobs_target_run_id", "jobs", ["target_run_id"], unique=False)
    op.create_index("ix_jobs_worker_id", "jobs", ["worker_id"], unique=False)

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("worker_id", name="pk_worker_heartbeats"),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")

    op.drop_index("ix_jobs_worker_id", table_name="jobs")
    op.drop_index("ix_jobs_target_run_id", table_name="jobs")

    op.alter_column("jobs", "status", server_default="pending")

    op.drop_column("jobs", "cancel_requested_at")
    op.drop_column("jobs", "worker_id")
    op.drop_column("jobs", "heartbeat_at")
    op.drop_column("jobs", "lease_expires_at")
    op.drop_column("jobs", "next_attempt_at")
    op.drop_column("jobs", "max_attempts")
    op.drop_column("jobs", "attempt_count")
    op.drop_column("jobs", "error_code")
    op.drop_column("jobs", "target_run_id")
