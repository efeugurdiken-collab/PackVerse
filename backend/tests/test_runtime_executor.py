"""Tests for app/runtime/executor.py (Sprint P6): the QUEUED -> RUNNING
-> LLM Gateway call -> COMPLETED/FAILED execution flow.

Every test routes through the network-free "fake" LLM provider - see
tests/test_llm_api.py's identical rationale. gateway.generate_and_persist
(app/services/llm_service.py) is exercised for real here, not mocked, so
these tests also incidentally confirm every agent run creates a real
llm_requests audit row.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.exceptions import LLMRateLimitError, LLMTimeoutError
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeProvider
from app.models.agent_run import AgentRun
from app.models.enums import AgentRunStatus, UserRole
from app.models.llm_request import LLMRequestRecord
from app.runtime.exceptions import AgentConfigurationError, InvalidRunTransitionError
from app.runtime.executor import execute_run
from app.runtime import service as runtime_service


def _settings(**overrides: object) -> Settings:
    return Settings(
        jwt_secret_key="x" * 32,
        llm_allowed_providers="fake",
        llm_default_provider="fake",
        **overrides,
    )


def _gateway(provider: FakeProvider, settings: Settings) -> LLMGateway:
    # retry_base_delay_seconds=0.0: these tests assert on the final
    # outcome, not retry timing - same rationale as tests/test_llm_api.py.
    return LLMGateway({"fake": provider}, settings, retry_base_delay_seconds=0.0)


async def test_successful_execution_marks_run_completed(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(response_content="the answer"), settings)

    result = await execute_run(
        db_session, gateway, settings, run=run, user_input="hello", context=None
    )

    assert result.status == AgentRunStatus.COMPLETED
    assert result.output_text == "the answer"
    assert result.provider == "fake"
    assert result.model == "fake-v1"
    assert result.started_at is not None
    assert result.completed_at is not None
    assert result.duration_ms is not None
    assert result.duration_ms >= 0
    assert result.error_code is None
    assert result.error_message is None


async def test_successful_execution_persists_token_and_cost_fields(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    result = await execute_run(
        db_session, gateway, settings, run=run, user_input="one two three", context=None
    )

    assert result.input_tokens is not None
    assert result.output_tokens is not None
    assert result.total_tokens == result.input_tokens + result.output_tokens


async def test_successful_execution_links_to_llm_request_audit_row(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    result = await execute_run(
        db_session, gateway, settings, run=run, user_input="hello", context=None
    )

    assert result.llm_request_id is not None
    record = await db_session.get(LLMRequestRecord, result.llm_request_id)
    assert record is not None
    assert record.status.value == "succeeded"


async def test_gateway_failure_marks_run_failed_and_reraises(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(fail_with=LLMRateLimitError("fake")), settings)

    with pytest.raises(LLMRateLimitError):
        await execute_run(db_session, gateway, settings, run=run, user_input="hello", context=None)

    await db_session.refresh(run)
    assert run.status == AgentRunStatus.FAILED
    assert run.error_code == "LLMRateLimitError"
    assert run.error_message is not None
    assert run.completed_at is not None


async def test_gateway_timeout_marks_run_failed(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(fail_with=LLMTimeoutError("fake")), settings)

    with pytest.raises(LLMTimeoutError):
        await execute_run(db_session, gateway, settings, run=run, user_input="hello", context=None)

    await db_session.refresh(run)
    assert run.status == AgentRunStatus.FAILED
    assert run.error_code == "LLMTimeoutError"


async def test_misconfigured_agent_marks_run_failed(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition(configuration_json={"model": "fake-v1"})  # no system_prompt
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    with pytest.raises(AgentConfigurationError):
        await execute_run(db_session, gateway, settings, run=run, user_input="hello", context=None)

    await db_session.refresh(run)
    assert run.status == AgentRunStatus.FAILED
    assert run.error_code == "AgentConfigurationError"


async def test_executing_a_non_queued_run_raises_invalid_transition(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = AgentRun(
        id=uuid.uuid4(),
        agent_id=agent.id,
        created_by_user_id=user.id,
        status=AgentRunStatus.CANCELLED,
    )
    db_session.add(run)
    await db_session.commit()
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    with pytest.raises(InvalidRunTransitionError):
        await execute_run(db_session, gateway, settings, run=run, user_input="hello", context=None)


async def test_context_reaches_the_provider_via_the_prompt(
    db_session: AsyncSession, make_user, make_agent_definition
) -> None:
    """FakeProvider's deterministic content echoes the last user
    message, so a context value showing up in the output confirms the
    executor actually threaded `context` through prompt_builder rather
    than dropping it."""
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    run = await runtime_service.create_run(
        db_session, agent_id=agent.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    result = await execute_run(
        db_session,
        gateway,
        settings,
        run=run,
        user_input="summarize",
        context={"topic": "unique-marker-xyz"},
    )

    assert result.output_text is not None
    assert "unique-marker-xyz" in result.output_text
