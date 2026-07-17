"""Workflow Run API schemas (Sprint P7).

WorkflowRunRead never exposes the workflow-level user_input/context -
app/models/workflow_run.py never stores them, mirroring
app/schemas/runtime.py's AgentRunRead not exposing user_input/context
(see that model's module docstring, and workflow_run.py's own docstring
for why). It DOES expose output_text, the same deliberate divergence P6
already made.

WorkflowStepRunRead DOES expose input_snapshot - app/models/
workflow_step_run.py DOES persist it, per the sprint's explicit
"input snapshot" persistence requirement (section 5) - see that model's
docstring for the full reasoning.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import WorkflowRunStatus, WorkflowStepRunStatus


class WorkflowRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: uuid.UUID
    user_input: str = Field(min_length=1)
    context: dict[str, object] | None = None


class WorkflowRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    created_by_user_id: uuid.UUID | None
    status: WorkflowRunStatus
    output_text: str | None
    error_code: str | None
    error_message: str | None
    duration_ms: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class WorkflowStepRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_run_id: uuid.UUID
    step_id: str
    step_order: int
    agent_id: uuid.UUID
    agent_run_id: uuid.UUID | None
    input_snapshot: str | None
    output_text: str | None
    status: WorkflowStepRunStatus
    error_code: str | None
    error_message: str | None
    duration_ms: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
