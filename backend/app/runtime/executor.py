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
same as every run before this sprint. This phase deliberately does not
persist a tool-call trace or aggregate token/cost across a multi-call
run's several llm_requests rows - see app/models/agent_run.py; a run's
provider/model/tokens/cost/llm_request_id always mirror its FINAL LLM
call only, the same shape this file has always had. A later phase may
add trace persistence and aggregation on top of this without changing
execute_run's public signature again.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

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
        )
    except (LLMError, RuntimeDomainError, MCPError) as exc:
        validate_transition(run.status, AgentRunStatus.FAILED)
        run.status = AgentRunStatus.FAILED
        run.error_code = type(exc).__name__
        run.error_message = str(exc)
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
    run.input_tokens = response.input_tokens
    run.output_tokens = response.output_tokens
    run.total_tokens = response.total_tokens
    run.estimated_cost_usd = response.estimated_cost_usd
    run.output_text = response.content
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
) -> GenerateResponse:
    """Sprint P9C1: the bounded LLM <-> MCP tool-call loop. Returns the
    final GenerateResponse once the model stops requesting tools. An
    agent with no mcp_server in configuration_json makes exactly one
    call here, identical to this file's pre-P9C1 behavior.

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

    for _ in range(settings.runtime_max_tool_iterations):
        response = await llm_service.generate_and_persist(
            db, gateway, settings, payload=payload, user_id=owner_id
        )
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
