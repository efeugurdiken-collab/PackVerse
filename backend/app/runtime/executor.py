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
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.exceptions import LLMError
from app.llm.gateway import LLMGateway
from app.models.agent_run import AgentRun
from app.models.enums import AgentRunStatus
from app.runtime.exceptions import RuntimeDomainError
from app.runtime.models import validate_transition
from app.runtime.prompt_builder import build_generate_request
from app.runtime.service import get_active_agent
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

    On any RuntimeDomainError (missing/inactive/misconfigured agent) or
    LLMError (timeout, rate limit, provider failure, ...), the run is
    persisted as FAILED with error_code/error_message set, and the
    exception is re-raised - app/api/v1/runs.py maps it to an HTTP
    status the same way app/api/v1/llm.py does for POST /llm/generate.
    The FAILED row still exists in the database either way; only the
    HTTP response for *this* request is an error.
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
        payload = build_generate_request(agent=agent, user_input=user_input, context=context)
        response = await llm_service.generate_and_persist(
            db, gateway, settings, payload=payload, user_id=owner_id
        )
    except (LLMError, RuntimeDomainError) as exc:
        validate_transition(run.status, AgentRunStatus.FAILED)
        run.status = AgentRunStatus.FAILED
        run.error_code = type(exc).__name__
        run.error_message = str(exc)
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.completed_at = datetime.now(timezone.utc)
        db.add(run)
        await db.commit()
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
