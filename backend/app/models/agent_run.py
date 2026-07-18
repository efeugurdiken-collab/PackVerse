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

provider/model/output_text always mirror the FINAL LLM call a run made
(reachable via llm_request_id) - this keeps GET /runs/{id} a
single-row read with no join. Every LLM call is created by
app.services.llm_service.generate_and_persist - the exact same function
POST /llm/generate uses - so every agent run also shows up in the P5
LLM usage/cost audit trail; nothing here reimplements that logic.

Sprint P9C1 added a bounded LLM<->MCP tool-call loop
(app/runtime/executor.py) for agents whose configuration_json names an
mcp_server - a single run can now involve several LLM calls, not just
one. Sprint P9C2 updated token/cost fields accordingly: input_tokens/
output_tokens/total_tokens/estimated_cost_usd are now the SUM across
every LLM call the run made (previously, and still for any run that
made exactly one call - i.e. every agent with no mcp_server configured,
still the common case - this is identical to "a denormalized copy of
the one corresponding llm_requests row"). estimated_cost_usd is
"sticky-None": if any call's own cost is unknown, the run's aggregate
is None too, never a fabricated partial total. tool_calls_json (added
in P9C2) persists the per-tool-call trace - see _run_tool_loop's
docstring for the exact entry shape - and, together with the aggregated
usage fields, is populated even on a FAILED run for whatever iterations
completed before the failure; llm_request_id/provider/model/output_text
are only ever set on a successful (COMPLETED) run, since there is no
"final call" to mirror when a run never reaches one.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    tool_calls_json: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSONB, nullable=True
    )
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
