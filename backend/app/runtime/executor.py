"""Execution orchestration (Sprint P6): QUEUED -> RUNNING -> LLM Gateway
call -> COMPLETED/FAILED.

Mirrors app/services/llm_service.py's generate_and_persist shape
exactly: persist state before the provider call, persist the outcome
(success or failure) after it, and on failure, persist first and then
re-raise rather than swallowing the error - app/api/v1/runs.py decides
what HTTP status a failure becomes, this module only owns the AgentRun's
own state and never touches HTTPException.

Deliberately calls app.services.llm_service.generate_and_persist rather
than app.llm.gateway.LLMGateway.generate directly: that function already
implements every piece "invoking LLM Gateway, storing outputs, recording
failures" needs - retry policy, cost estimation, and llm_requests
persistence for the P5 audit trail. Reimplementing that here would be
exactly the "duplicated ... logic" the sprint says to avoid.

Sprint P9C1 extends the single LLM Gateway call into a bounded
LLM <-> MCP tool-call loop (see _run_tool_loop below) when the agent's
configuration_json names an mcp_server - see
app/runtime/prompt_builder.py's convention. An agent with no mcp_server
configured is completely unaffected: exactly one LLM Gateway call, the
same as every run before this sprint.

Sprint P9C2 adds trace persistence and usage aggregation on top of
P9C1's loop, via _ToolLoopUsage (accumulated by _run_tool_loop as it
goes) and _persist_tool_loop_usage (which writes it onto `run` - shared
by both the success and failure branches below, so a run that fails
partway through a multi-call loop still persists whatever tool calls
and token/cost usage completed before the failure). See
app/models/agent_run.py's module docstring for the exact field
semantics this introduces.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.exceptions import LLMError
from app.llm.gateway import LLMGateway
from app.mcp.client import MCPClient
from app.mcp.exceptions import MCPError
from app.mcp.factory import build_mcp_client
from app.models.agent_definition import AgentDefinition
from app.models.agent_run import AgentRun
from app.models.enums import AgentRunStatus
from app.runtime.exceptions import RuntimeDomainError, ToolLoopLimitExceededError
from app.runtime.models import validate_transition
from app.runtime.prompt_builder import build_generate_request
from app.runtime.service import get_active_agent
from app.schemas.llm import GenerateResponse, MessageIn, ToolDefinitionIn
from app.services import llm_service


@dataclass
class _ToolLoopUsage:
    """Accumulates trace/token/cost data across every LLM call
    _run_tool_loop makes for one run (Sprint P9C2) - mutated as the loop
    goes, and read by execute_run after the loop either returns or
    raises, via _persist_tool_loop_usage below.

    total_cost_usd is "sticky-None": once any single call's own cost is
    unknown, the whole run's aggregate becomes (and stays) unknown too -
    never a fabricated partial total. calls_made distinguishes "zero
    calls happened at all" (e.g. an unconfigured mcp_server failing
    before any call was attempted) from "calls happened but summed to
    zero", so _persist_tool_loop_usage can leave every field None in the
    former case rather than writing a misleading 0/[].
    """

    trace: list[dict[str, object]] = field(default_factory=list)
    calls_made: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: Decimal | None = Decimal("0")

    def record_call(self, response: GenerateResponse) -> None:
        self.calls_made += 1
        self.total_input_tokens += response.input_tokens
        self.total_output_tokens += response.output_tokens
        if response.estimated_cost_usd is None:
            self.total_cost_usd = None
        elif self.total_cost_usd is not None:
            self.total_cost_usd += response.estimated_cost_usd


def _persist_tool_loop_usage(run: AgentRun, usage: _ToolLoopUsage) -> None:
    """Writes accumulated token/cost/trace fields onto `run` - shared by
    both execute_run's success and failure branches so the two paths
    can never drift apart. A no-op if no LLM call ever completed
    (usage.calls_made == 0), leaving those fields at their natural
    None default rather than a fabricated zero/empty list."""
    if usage.calls_made == 0:
        return
    run.input_tokens = usage.total_input_tokens
    run.output_tokens = usage.total_output_tokens
    run.total_tokens = usage.total_input_tokens + usage.total_output_tokens
    run.estimated_cost_usd = usage.total_cost_usd
    run.tool_calls_json = usage.trace or None


async def execute_run(
    db: AsyncSession,
    gateway: LLMGateway,
    settings: Settings,
    *,
    run: AgentRun,
    user_input: str,
    context: dict[str, object] | None,
) -> AgentRun:
    """Runs `run` (which must currently be QUEUED - raises
    InvalidRunTransitionError otherwise, e.g. if it was already
    cancelled) through the full Running -> Load Agent Definition ->
    Build Prompt -> Call LLM Gateway -> Store Output -> Completed/Failed
    flow described in the sprint spec.

    On any RuntimeDomainError (missing/inactive/misconfigured agent),
    LLMError (timeout, rate limit, provider failure, ...), or (Sprint
    P9C1) MCPError (an mcp_server tool call/lookup failure), the run is
    persisted as FAILED with error_code/error_message set, and the
    exception is re-raised - app/api/v1/runs.py maps it to an HTTP
    status the same way app/api/v1/llm.py does for POST /llm/generate.
    The FAILED row still exists in the database either way; only the
    HTTP response for *this* request is an error.

    An MCPError specifically is re-raised as a RuntimeDomainError (not
    the original MCPError type) - see the except block below for why.
    """
    validate_transition(run.status, AgentRunStatus.RUNNING)
    run.status = AgentRunStatus.RUNNING
    run.started_at = datetime.now(timezone.utc)
    db.add(run)
    await db.commit()

    started = time.monotonic()
    usage = _ToolLoopUsage()
    try:
        owner_id = run.created_by_user_id
        if owner_id is None:
            # Defensive, not expected in practice: create_run() always
            # sets this from the caller's current_user.id; it can only
            # become None later if that user row is deleted (the FK is
            # ON DELETE SET NULL - see app/models/agent_run.py). A run
            # whose owner has since disappeared has no valid id left to
            # attribute a new llm_requests row to.
            raise RuntimeDomainError(f"Agent run {run.id} has no owning user to attribute usage to")
        agent = await get_active_agent(db, run.agent_id)
        response = await _run_tool_loop(
            db,
            gateway,
            settings,
            agent=agent,
            user_input=user_input,
            context=context,
            owner_id=owner_id,
            usage=usage,
        )
    except (LLMError, RuntimeDomainError, MCPError) as exc:
        validate_transition(run.status, AgentRunStatus.FAILED)
        run.status = AgentRunStatus.FAILED
        run.error_code = type(exc).__name__
        run.error_message = str(exc)
        _persist_tool_loop_usage(run, usage)
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.completed_at = datetime.now(timezone.utc)
        db.add(run)
        await db.commit()
        if isinstance(exc, MCPError):
            # app/worker/dispatch.py's _DOMAIN_ERROR_TYPES (Sprint P8)
            # predates app.mcp - re-raising as RuntimeDomainError lets
            # that existing, unmodified tuple still route this straight
            # to mark_failed instead of a wasted worker-level retry.
            # run.error_code above already preserved the real MCP
            # exception's own class name.
            raise RuntimeDomainError(str(exc)) from exc
        raise

    validate_transition(run.status, AgentRunStatus.COMPLETED)
    run.status = AgentRunStatus.COMPLETED
    run.llm_request_id = uuid.UUID(response.request_id)
    run.provider = response.provider
    run.model = response.model
    run.output_text = response.content
    _persist_tool_loop_usage(run, usage)
    run.duration_ms = int((time.monotonic() - started) * 1000)
    run.completed_at = datetime.now(timezone.utc)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _run_tool_loop(
    db: AsyncSession,
    gateway: LLMGateway,
    settings: Settings,
    *,
    agent: AgentDefinition,
    user_input: str,
    context: dict[str, object] | None,
    owner_id: uuid.UUID,
    usage: _ToolLoopUsage,
) -> GenerateResponse:
    """Sprint P9C1: the bounded LLM <-> MCP tool-call loop. Returns the
    final GenerateResponse once the model stops requesting tools. An
    agent with no mcp_server in configuration_json makes exactly one
    call here, identical to this file's pre-P9C1 behavior.

    Sprint P9C2: mutates `usage` as it goes - one record_call() per LLM
    call that actually returns (a call that raises contributes nothing),
    and one trace entry appended immediately after each individual tool
    call succeeds, before the next tool call (or iteration) is
    attempted. This is what makes partial trace/usage survive a failure
    partway through a batch of tool calls or partway through the loop:
    whatever was appended before the failure is already sitting in
    `usage`, which execute_run's except block reads regardless of how
    this function exits.

    Tool results are threaded back as a synthesized plain-text `user`
    message ("Tool 'x' was called with {...} and returned: ...") rather
    than a provider's native tool-result message/content-block shape -
    app.schemas.llm.MessageRole has no "tool" role and this phase does
    not add one. A known, deliberate simplification - see the Sprint
    P9C1 report.

    Raises RuntimeDomainError if the model somehow returns tool_calls
    without any tools having been offered (defensive - not expected in
    practice, since tools are only ever attached when mcp_server is
    set), and ToolLoopLimitExceededError if the model still wants a
    tool after settings.runtime_max_tool_iterations calls. Both are
    RuntimeDomainError (sub)classes, so execute_run's except clause
    above handles them with no special-casing.
    """
    mcp_server_name = agent.configuration_json.get("mcp_server")
    tools: list[ToolDefinitionIn] | None = None
    mcp_client: MCPClient | None = None
    if isinstance(mcp_server_name, str) and mcp_server_name:
        mcp_client = build_mcp_client(mcp_server_name, settings)
        mcp_tools = await mcp_client.list_tools()
        tools = [
            ToolDefinitionIn(name=t.name, description=t.description, input_schema=t.input_schema)
            for t in mcp_tools
        ]

    payload = build_generate_request(
        agent=agent, user_input=user_input, context=context, tools=tools
    )

    for iteration in range(1, settings.runtime_max_tool_iterations + 1):
        response = await llm_service.generate_and_persist(
            db, gateway, settings, payload=payload, user_id=owner_id
        )
        usage.record_call(response)
        if not response.tool_calls:
            return response

        if mcp_client is None:
            raise RuntimeDomainError(
                f"Agent {agent.id} received tool_calls from the provider but has no "
                "mcp_server configured to execute them"
            )

        follow_up_messages = list(payload.messages)
        for call in response.tool_calls:
            result = await mcp_client.call_tool(call.name, call.arguments)
            usage.trace.append(
                {
                    "iteration": iteration,
                    "llm_request_id": response.request_id,
                    "tool_name": call.name,
                    "arguments": call.arguments,
                    "result": result.content,
                    "is_error": result.is_error,
                }
            )
            follow_up_messages.append(
                MessageIn(
                    role="user",
                    content=(
                        f"Tool {call.name!r} was called with {call.arguments!r} and "
                        f"returned: {result.content}"
                    ),
                )
            )
        payload = payload.model_copy(update={"messages": follow_up_messages})

    raise ToolLoopLimitExceededError(agent.id, settings.runtime_max_tool_iterations)
