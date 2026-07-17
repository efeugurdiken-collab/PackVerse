"""Tests for app/workflows/executor.py (Sprint P7): the QUEUED -> RUNNING
-> sequential per-step P6 runtime execution -> COMPLETED/FAILED flow.

Every test routes through the network-free "fake" LLM provider - same
rationale as tests/test_runtime_executor.py. FakeProvider's deterministic
echo (`[fake:{model}] {last_user_message}`) is used throughout to assert
on exactly what input each step actually received, confirming output
propagation without needing a second fake provider variant.
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
from app.models.enums import AgentStatus, UserRole, WorkflowRunStatus, WorkflowStepRunStatus
from app.runtime.exceptions import AgentNotActiveError
from app.workflows import service as workflow_service
from app.workflows.executor import execute_workflow_run


def _settings(**overrides: object) -> Settings:
    return Settings(
        jwt_secret_key="x" * 32,
        llm_allowed_providers="fake",
        llm_default_provider="fake",
        **overrides,
    )


def _gateway(provider: FakeProvider, settings: Settings) -> LLMGateway:
    return LLMGateway({"fake": provider}, settings, retry_base_delay_seconds=0.0)


def _one_step_definition(agent_id: uuid.UUID) -> list[dict[str, object]]:
    return [{"step_id": "only", "name": "Only", "agent_definition_id": str(agent_id), "order": 1}]


def _two_step_definition(agent1_id: uuid.UUID, agent2_id: uuid.UUID) -> list[dict[str, object]]:
    return [
        {"step_id": "first", "name": "First", "agent_definition_id": str(agent1_id), "order": 1},
        {"step_id": "second", "name": "Second", "agent_definition_id": str(agent2_id), "order": 2},
    ]


async def test_one_step_success_marks_run_and_step_completed(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    result = await execute_workflow_run(
        db_session, gateway, settings, run=run, workflow_user_input="hello", context=None
    )

    assert result.status == WorkflowRunStatus.COMPLETED
    assert result.output_text == "[fake:fake-v1] hello"
    assert result.started_at is not None
    assert result.completed_at is not None
    assert result.duration_ms is not None
    assert result.duration_ms >= 0

    steps = await workflow_service.get_steps(db_session, run.id, user)
    assert len(steps) == 1
    assert steps[0].status == WorkflowStepRunStatus.COMPLETED
    assert steps[0].input_snapshot == "hello"
    assert steps[0].output_text == "[fake:fake-v1] hello"
    assert steps[0].agent_run_id is not None


async def test_multi_step_success_propagates_output_between_steps(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent1 = await make_agent_definition()
    agent2 = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_two_step_definition(agent1.id, agent2.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    result = await execute_workflow_run(
        db_session, gateway, settings, run=run, workflow_user_input="hello", context=None
    )

    assert result.status == WorkflowRunStatus.COMPLETED
    steps = await workflow_service.get_steps(db_session, run.id, user)
    by_id = {s.step_id: s for s in steps}

    # Step 1 receives the original workflow input.
    assert by_id["first"].input_snapshot == "hello"
    assert by_id["first"].output_text == "[fake:fake-v1] hello"
    # Step 2 (default previous_output mapping) receives step 1's output.
    assert by_id["second"].input_snapshot == "[fake:fake-v1] hello"
    assert by_id["second"].output_text == "[fake:fake-v1] [fake:fake-v1] hello"

    # Final workflow output is the LAST completed step's output.
    assert result.output_text == by_id["second"].output_text


async def test_named_step_output_mapping_reaches_the_correct_step(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent1 = await make_agent_definition()
    agent2 = await make_agent_definition()
    agent3 = await make_agent_definition()
    workflow = await make_workflow_definition(
        steps=[
            {"step_id": "a", "name": "A", "agent_definition_id": str(agent1.id), "order": 1},
            {"step_id": "b", "name": "B", "agent_definition_id": str(agent2.id), "order": 2},
            {
                "step_id": "c",
                "name": "C",
                "agent_definition_id": str(agent3.id),
                "order": 3,
                "input_mapping": {"source": "step_output", "step_id": "a"},
            },
        ]
    )
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    await execute_workflow_run(
        db_session, gateway, settings, run=run, workflow_user_input="hello", context=None
    )

    steps = await workflow_service.get_steps(db_session, run.id, user)
    by_id = {s.step_id: s for s in steps}
    # Step c references step a's output directly, skipping step b.
    assert by_id["c"].input_snapshot == by_id["a"].output_text


async def test_first_step_failure_fails_run_and_skips_remaining_steps(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent1 = await make_agent_definition()
    agent2 = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_two_step_definition(agent1.id, agent2.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(fail_with=LLMRateLimitError("fake")), settings)

    with pytest.raises(LLMRateLimitError):
        await execute_workflow_run(
            db_session, gateway, settings, run=run, workflow_user_input="hello", context=None
        )

    await db_session.refresh(run)
    assert run.status == WorkflowRunStatus.FAILED
    assert run.error_code == "LLMRateLimitError"
    assert run.completed_at is not None

    steps = await workflow_service.get_steps(db_session, run.id, user)
    by_id = {s.step_id: s for s in steps}
    assert by_id["first"].status == WorkflowStepRunStatus.FAILED
    assert by_id["first"].error_code == "LLMRateLimitError"
    assert by_id["second"].status == WorkflowStepRunStatus.SKIPPED


async def test_middle_step_failure_fails_run_and_skips_only_later_steps(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent1 = await make_agent_definition()
    agent2 = await make_agent_definition()
    agent3 = await make_agent_definition()
    workflow = await make_workflow_definition(
        steps=[
            {"step_id": "a", "name": "A", "agent_definition_id": str(agent1.id), "order": 1},
            {"step_id": "b", "name": "B", "agent_definition_id": str(agent2.id), "order": 2},
            {"step_id": "c", "name": "C", "agent_definition_id": str(agent3.id), "order": 3},
        ]
    )
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )
    settings = _settings()
    # FakeProvider has no per-agent routing, so a genuine middle-step
    # (not first-step) failure is exercised here by making the *second*
    # step's agent inactive after workflow-run creation but before
    # execution - get_active_agent is re-checked at each step's execution
    # time (via app.runtime.service.create_run), so step "a" still
    # completes successfully and only step "b" fails.
    gateway = _gateway(FakeProvider(), settings)
    agent2.status = AgentStatus.DEPRECATED
    db_session.add(agent2)
    await db_session.commit()

    with pytest.raises(AgentNotActiveError):
        await execute_workflow_run(
            db_session, gateway, settings, run=run, workflow_user_input="hello", context=None
        )

    await db_session.refresh(run)
    assert run.status == WorkflowRunStatus.FAILED

    steps = await workflow_service.get_steps(db_session, run.id, user)
    by_id = {s.step_id: s for s in steps}
    assert by_id["a"].status == WorkflowStepRunStatus.COMPLETED
    assert by_id["b"].status == WorkflowStepRunStatus.FAILED
    assert by_id["b"].error_code == "AgentNotActiveError"
    assert by_id["c"].status == WorkflowStepRunStatus.SKIPPED


async def test_timeout_failure_marks_run_and_step_failed(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(fail_with=LLMTimeoutError("fake")), settings)

    with pytest.raises(LLMTimeoutError):
        await execute_workflow_run(
            db_session, gateway, settings, run=run, workflow_user_input="hello", context=None
        )

    await db_session.refresh(run)
    assert run.status == WorkflowRunStatus.FAILED
    assert run.error_code == "LLMTimeoutError"

    steps = await workflow_service.get_steps(db_session, run.id, user)
    assert steps[0].status == WorkflowStepRunStatus.FAILED
    assert steps[0].error_code == "LLMTimeoutError"


async def test_each_step_links_to_a_real_p6_agent_run(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent1 = await make_agent_definition()
    agent2 = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_two_step_definition(agent1.id, agent2.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    await execute_workflow_run(
        db_session, gateway, settings, run=run, workflow_user_input="hello", context=None
    )

    steps = await workflow_service.get_steps(db_session, run.id, user)
    for step in steps:
        assert step.agent_run_id is not None
        agent_run = await db_session.get(AgentRun, step.agent_run_id)
        assert agent_run is not None
        assert agent_run.output_text == step.output_text


async def test_context_reaches_every_steps_prompt(
    db_session: AsyncSession, make_user, make_agent_definition, make_workflow_definition
) -> None:
    user = await make_user(role=UserRole.OPERATOR)
    agent = await make_agent_definition()
    workflow = await make_workflow_definition(steps=_one_step_definition(agent.id))
    run = await workflow_service.create_workflow_run(
        db_session, workflow_id=workflow.id, created_by_user_id=user.id
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    result = await execute_workflow_run(
        db_session,
        gateway,
        settings,
        run=run,
        workflow_user_input="summarize",
        context={"topic": "unique-marker-xyz"},
    )

    assert result.output_text is not None
    assert "unique-marker-xyz" in result.output_text
