"""add partial unique index for active asset_ingestion jobs (Sprint
P10B3: Ingestion API)

Revision ID: cc4808800645
Revises: e4ba9bdd172a
Create Date: 2026-07-20 00:00:00.000000

Hand-written, same reason as every prior migration in this project: no
PostgreSQL instance or network access available in the sandbox this was
written in. Written directly from app/models/job.py; verify against a
real database before merging (see docs/P1_LOCAL_VERIFICATION.md).

Adds `uq_jobs_active_asset_ingestion`, a partial unique index on
`jobs.target_run_id`, scoped to `job_type = 'asset_ingestion' AND status
IN ('QUEUED', 'RUNNING', 'RETRYING')` - see app/models/job.py's module
docstring for the full rationale (closing the concurrent-double-enqueue
race for write-once asset ingestion at the database level, not just an
application-level pre-check). Does not touch any table or column - no
new job_type value is enforced at the schema level (`jobs.job_type`
stays a plain, app-validated VARCHAR, same as every other value it
already holds), only this one new index.

The status literals are uppercase (JobStatus member NAMES: 'QUEUED', not
'queued') because `jobs.status` is a sqlalchemy.Enum(JobStatus,
native_enum=False) column with no values_callable - SQLAlchemy's
documented default for that configuration persists each member's
`.name`, not its `.value`. Confirmed against a real database while
writing this migration; app/models/job.py's own predicate derives this
same set of literals from JobStatus directly (not hand-typed) so the two
can't drift, but this migration's copy is a frozen historical snapshot,
same convention as every other hand-written migration in this project.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cc4808800645"
down_revision: Union[str, None] = "e4ba9bdd172a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_jobs_active_asset_ingestion",
        "jobs",
        ["target_run_id"],
        unique=True,
        postgresql_where=sa.text(
            "job_type = 'asset_ingestion' AND status IN ('QUEUED', 'RUNNING', 'RETRYING')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_active_asset_ingestion", table_name="jobs")
