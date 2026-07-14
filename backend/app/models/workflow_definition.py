"""WorkflowDefinition model - the persisted, versioned config for a Workflow.

Corresponds to the Workflow specifications in the PackVerse OS vault
(06 Workflows/). Populated starting with the Product Factory sprint (P9),
which executes workflows by reading their definition_json rather than
hardcoding each workflow's step sequence.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import WorkflowStatus


class WorkflowDefinition(Base, TimestampMixin):
    __tablename__ = "workflow_definitions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1.0")
    status: Mapped[WorkflowStatus] = mapped_column(
        SAEnum(WorkflowStatus, name="workflow_status", native_enum=False, validate_strings=True),
        nullable=False,
        default=WorkflowStatus.DRAFT,
        index=True,
    )
    definition_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<WorkflowDefinition id={self.id} name={self.name!r} status={self.status}>"
