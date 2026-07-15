"""LLMRequestRecord - persisted metadata for a single LLM Gateway call
(Sprint P5).

Deliberately does not store the prompt or the generated content - only
routing/usage/cost/latency metadata. See the sprint spec's "Do not
persist full prompts or generated content by default"; this sprint does
not implement an opt-in diagnostic-content column at all, so there is
nothing here that could ever leak either.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import LLMRequestStatus

if TYPE_CHECKING:
    from app.models.user import User


class LLMRequestRecord(Base, TimestampMixin):
    __tablename__ = "llm_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[LLMRequestStatus] = mapped_column(
        SAEnum(
            LLMRequestStatus, name="llm_request_status", native_enum=False, validate_strings=True
        ),
        nullable=False,
        default=LLMRequestStatus.PENDING,
        index=True,
    )
    request_metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    response_metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<LLMRequestRecord id={self.id} provider={self.provider!r} "
            f"model={self.model!r} status={self.status}>"
        )
