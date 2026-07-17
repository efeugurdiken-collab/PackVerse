"""WorkflowStepRun model - one row per step of a WorkflowRun (Sprint P7).

Unlike WorkflowRun (and app/models/agent_run.py before it), this table
DOES persist input_snapshot - the sprint spec explicitly lists "input
snapshot" in its required persistence fields (section 5), unlike the
workflow-run-level raw user_input/context, which the spec leaves to this
codebase's existing privacy posture and which this sprint therefore does
NOT persist (see workflow_run.py's docstring). input_snapshot is the
*resolved* input actually sent to the step's agent (built by
app/workflows/input_builder.py) - for steps after the first, this is
typically a prior step's own already-persisted output_text, not fresh
user-supplied secrets, which is part of why persisting it here is a
reasonable, bounded exception rather than a blanket prompt-logging
policy.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import WorkflowStepRunStatus

if TYPE_CHECKING:
    from app.models.agent_definition import AgentDefinition
    from app.models.agent_run import AgentRun
    from app.models.workflow_run import WorkflowRun


class WorkflowStepRun(Base, TimestampMixin):
    __tablename__ = "workflow_step_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str] = mapped_column(String(128), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_definitions.id"), nullable=False, index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    input_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[WorkflowStepRunStatus] = mapped_column(
        SAEnum(
            WorkflowStepRunStatus,
            name="workflow_step_run_status",
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
        default=WorkflowStepRunStatus.PENDING,
        index=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="step_runs")
    agent: Mapped[AgentDefinition] = relationship()
    agent_run: Mapped[AgentRun | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<WorkflowStepRun id={self.id} workflow_run_id={self.workflow_run_id} "
            f"step_id={self.step_id!r} status={self.status}>"
        )
