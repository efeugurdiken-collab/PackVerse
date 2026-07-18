"""Agent Run API schemas (Sprint P6).

AgentRunRead never exposes user_input/context - app/models/agent_run.py
never stores them, for the same reason app/schemas/llm.py's
LLMRequestRead never exposes prompt/response content (see that model's
module docstring); there is nothing here to accidentally leak. It DOES
expose output_text - a deliberate divergence from the P5 pattern, since
the P6 sprint spec explicitly lists "output" as a field to persist.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AgentRunStatus


class AgentRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    user_input: str = Field(min_length=1)
    context: dict[str, object] | None = None


class ToolCallTrace(BaseModel):
    """One entry of AgentRun.tool_calls_json (Sprint P9C2) - one MCP
    tool call made during this run's bounded tool-call loop. See
    app/runtime/executor.py's _run_tool_loop for exactly when/how each
    entry is appended, including on a run that ultimately failed."""

    iteration: int
    llm_request_id: uuid.UUID | None
    tool_name: str
    arguments: dict[str, object]
    result: str
    is_error: bool


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    created_by_user_id: uuid.UUID | None
    status: AgentRunStatus
    llm_request_id: uuid.UUID | None
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    estimated_cost_usd: Decimal | None
    output_text: str | None
    tool_calls_json: list[ToolCallTrace] | None
    error_code: str | None
    error_message: str | None
    duration_ms: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
