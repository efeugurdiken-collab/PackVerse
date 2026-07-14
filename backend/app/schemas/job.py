"""Job API schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import JobStatus


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_type: str = Field(min_length=1, max_length=128)
    input_json: dict[str, object] = Field(default_factory=dict)


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_type: str
    status: JobStatus
    input_json: dict[str, object]
    output_json: dict[str, object] | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
