"""AgentRun model - a single execution of an AgentDefinition through the
LLM Gateway (Sprint P6: AI Runtime).

Architectural note (see the Sprint P6 report's "Important architectural
decisions" for the full write-up): unlike llm_requests (Sprint P5),
which deliberately excludes prompt and generated content, this sprint's
spec explicitly lists "output" among the fields to persist - so
output_text IS stored here. The raw user_input/context that produced it
are NOT persisted as columns, consistent with the security posture
llm_requests already established (no raw prompt storage) - the P6
spec's own persistence list doesn't ask for them either, and there is no
reason to be less careful with user-supplied text here than P5 was.

provider/model/token/cost fields are a denormalized copy of the
corresponding llm_requests row (reachable via llm_request_id) - this
keeps GET /runs/{id} a single-row read with no join, matching the
spec's "Persist: ... provider, model, token usage, estimated cost ..."
literally, while llm_request_id still lets you reach the full audit row
if ever needed. That row is created by
app.services.llm_service.generate_and_persist - the exact same function
POST /llm/generate uses - so every agent run also shows up in the P5
LLM usage/cost audit trail; nothing here reimplements that logic.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import AgentRunStatus

if TYPE_CHECKING:
    from app.models.agent_definition import AgentDefinition
    from app.models.llm_request import LLMRequestRecord
    from app.models.user import User


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # No ondelete here (defaults to Postgres' NO ACTION/RESTRICT-like
    # behavior) - deliberate: an AgentDefinition with run history should
    # not be silently deletable out from under that history. There is no
    # AgentDefinition delete endpoint in this codebase yet anyway.
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_definitions.id"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        SAEnum(AgentRunStatus, name="agent_run_status", native_enum=False, validate_strings=True),
        nullable=False,
        default=AgentRunStatus.QUEUED,
        index=True,
    )
    llm_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped[AgentDefinition] = relationship()
    created_by: Mapped[User | None] = relationship()
    llm_request: Mapped[LLMRequestRecord | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<AgentRun id={self.id} agent_id={self.agent_id} status={self.status}>"
