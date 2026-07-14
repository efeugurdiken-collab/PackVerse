"""AgentDefinition model - the persisted, versioned config for an Agent.

Corresponds to the Agent specifications in the PackVerse OS vault
(05 Agents/). This table is the runtime source of truth once the AI
Runtime sprint (P6) loads Agent behavior dynamically instead of hardcoding it.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import AgentStatus


class AgentDefinition(Base, TimestampMixin):
    __tablename__ = "agent_definitions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1.0")
    status: Mapped[AgentStatus] = mapped_column(
        SAEnum(AgentStatus, name="agent_status", native_enum=False, validate_strings=True),
        nullable=False,
        default=AgentStatus.DRAFT,
        index=True,
    )
    configuration_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<AgentDefinition id={self.id} name={self.name!r} status={self.status}>"
