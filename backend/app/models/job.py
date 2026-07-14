"""Job model - a unit of asynchronous work (Agent runs, batch processing).

Introduced now, ahead of the AI Runtime sprint (P6), so the schema and
migration history stays additive rather than requiring a disruptive
schema change once Agents start enqueueing real jobs.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import DateTime, String, Text
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
        default=JobStatus.PENDING,
        index=True,
    )
    input_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    output_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Job id={self.id} job_type={self.job_type!r} status={self.status}>"
