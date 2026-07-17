"""Job model - a unit of asynchronous work (Sprint P8: Asynchronous Job
Execution).

Introduced back in P2, ahead of the AI Runtime sprint (P6), so the
schema and migration history would stay additive rather than requiring
a disruptive schema change once Agents/Workflows started enqueueing real
jobs - see git history for the original placeholder. This sprint is the
first to actually populate it: `job_type` is `"agent_run"` or
`"workflow_run"`; `target_run_id` points at the corresponding
`agent_runs.id` or `workflow_runs.id` row (no FK constraint - it's
polymorphic across two tables, resolved by `job_type` at read time, the
same "no FK for a polymorphic reference" tradeoff Django/Rails ORMs call
a generic foreign key).

`input_json` DOES store the caller's raw `user_input`/`context`
(`{"user_input": "...", "context": {...} | null}`) - a deliberate,
necessary divergence from `agent_runs`/`workflow_runs`, which still
never persist it themselves (see those models' docstrings). Synchronous
P6/P7 execution never needed to persist it: the same HTTP request that
created the run also executed it, so `user_input` only ever lived in
memory. A durable job queue breaks that assumption - the worker that
executes this job may run in a different process, possibly much later,
so *something* durable must carry the input across that gap. `input_json`
already existed for exactly this ("a unit of asynchronous work"), and
the sprint's own privacy carve-out ("do not persist secrets,
credentials, or raw provider payloads") does not forbid it - `user_input`
is the caller's own operational request content, not a secret/credential
/provider payload. See the Sprint P8 report's Known Limitations for the
resulting retention tradeoff (no TTL/cleanup policy is implemented this
sprint).

`output_json` is deliberately left unused (always null) - the owning
AgentRun/WorkflowRun row is the single source of truth for output, per
the "each domain owns its own persistence" pattern already established;
duplicating it here would just be another place for it to drift.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import JobStatus


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status", native_enum=False, validate_strings=True),
        nullable=False,
        default=JobStatus.QUEUED,
        index=True,
    )
    # Polymorphic reference to agent_runs.id or workflow_runs.id,
    # disambiguated by job_type - see module docstring.
    target_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    input_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    output_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # When a RETRYING job becomes eligible to be claimed again (backoff).
    # Null for QUEUED jobs (immediately eligible) and terminal jobs.
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set together whenever a worker claims this job; a RUNNING job whose
    # lease_expires_at is in the past is "stale" - see
    # app/jobs/queue.py's recover_stale_jobs.
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # Set by app/jobs/service.py's cancel_*_run when a RUNNING job cannot
    # be cancelled immediately (an in-flight provider request can't be
    # interrupted) - a cooperative signal, not a status transition, so it
    # never races the worker's own status writes on this same row. A
    # workflow-run job's worker checks this between steps (see
    # app/workflows/executor.py's cancellation_check parameter); an
    # agent-run job's worker never checks it (a single provider call has
    # no "between steps" checkpoint) - see the Sprint P8 report's
    # documented cancellation limitation.
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Job id={self.id} job_type={self.job_type!r} status={self.status}>"
