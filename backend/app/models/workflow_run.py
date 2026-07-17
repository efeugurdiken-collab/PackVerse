"""WorkflowRun model - a single execution of a WorkflowDefinition through
its ordered agent steps (Sprint P7: Workflow Orchestration).

Deliberately does NOT persist the raw workflow-level user_input/context -
consistent with the security posture app/models/agent_run.py already
established for Sprint P6 (and llm_requests before it). The sprint spec
explicitly authorizes this judgment call: "If input persistence
conflicts with existing privacy rules, store only the minimum safe
representation and document the decision." output_text (the final
step's output) IS stored, the same deliberate divergence P6 already
made for its own output_text - see that model's docstring for the
reasoning, which applies unchanged here.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import WorkflowRunStatus

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workflow_definition import WorkflowDefinition
    from app.models.workflow_step_run import WorkflowStepRun


class WorkflowRun(Base, TimestampMixin):
    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # No ondelete here (defaults to Postgres' NO ACTION/RESTRICT-like
    # behavior) - same rationale as agent_runs.agent_id: a
    # WorkflowDefinition with run history should not be silently
    # deletable out from under it. There is no WorkflowDefinition delete
    # endpoint in this codebase.
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definitions.id"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[WorkflowRunStatus] = mapped_column(
        SAEnum(
            WorkflowRunStatus, name="workflow_run_status", native_enum=False, validate_strings=True
        ),
        nullable=False,
        default=WorkflowRunStatus.QUEUED,
        index=True,
    )
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow: Mapped[WorkflowDefinition] = relationship()
    created_by: Mapped[User | None] = relationship()
    step_runs: Mapped[list[WorkflowStepRun]] = relationship(
        back_populates="workflow_run",
        cascade="all, delete-orphan",
        order_by="WorkflowStepRun.step_order",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<WorkflowRun id={self.id} workflow_id={self.workflow_id} status={self.status}>"
