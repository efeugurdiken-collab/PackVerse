"""Tests for the job-queue service layer (Sprint P8): app/jobs/service.py
- enqueueing (including the atomic single-commit enqueue-safety property)
and the three-tier cancellation design. Sprint P10B3 adds
enqueue_asset_ingestion, get_latest_asset_ingestion_job, and the
uq_jobs_active_asset_ingestion partial unique index's duplicate-queueing
guarantee.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.jobs import queue, service as job_service
from app.jobs.exceptions import JobAlreadyRunningError
from app.models.enums import AgentRunStatus, JobStatus, UserRole, WorkflowRunStatus
from app.models.job import Job
from app.runtime.exceptions import AgentRunNotFoundError, RuntimeDomainError
from app.services.exceptions import (
    AssetAlreadyIngestedError,
    AssetIngestionAlreadyQueuedError,
    AssetNotFoundError,
    AssetNotIngestableError,
)
from app.workflows.exceptions import WorkflowDomainError, WorkflowRunNotFoundError


@pytest.fixture
async def owner(make_user):
    return await make_user(role=UserRole.OPERATOR)


@pytest.fixture
async def other_user(make_user):
    return await make_user(role=UserRole.OPERATOR)


# --- enqueue_agent_run ------------------------------------------------


async def test_enqueue_agent_run_creates_queued_run_and_queued_job(
    db_session, owner, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    run, job = await job_service.enqueue_agent_run(
        db_session,
        agent_id=agent.id,
        created_by_user_id=owner.id,
        user_input="hello",
        context=None,
        max_attempts=3,
    )
    assert run.status == AgentRunStatus.QUEUED
    assert job.status == JobStatus.QUEUED
    assert job.job_type == job_service.AGENT_RUN_JOB_TYPE
    assert job.target_run_id == run.id
    assert job.input_json == {"user_input": "hello", "context": None}
    assert job.max_attempts == 3


async def test_enqueue_agent_run_persists_both_rows_in_one_commit(
    db_session, owner, make_agent_definition
) -> None:
    """Both the run and its paired job must exist after enqueueing - the
    "no naive dual write" property means there is no window where one
    exists without the other."""
    agent = await make_agent_definition()
    run, job = await job_service.enqueue_agent_run(
        db_session,
        agent_id=agent.id,
        created_by_user_id=owner.id,
        user_input="hello",
        context=None,
        max_attempts=3,
    )
    result = await db_session.execute(select(Job).where(Job.target_run_id == run.id))
    persisted_job = result.scalar_one()
    assert persisted_job.id == job.id


async def test_enqueue_agent_run_with_unknown_agent_raises_and_creates_nothing(
    db_session, owner
) -> None:
    with pytest.raises(RuntimeDomainError):
        await job_service.enqueue_agent_run(
            db_session,
            agent_id=uuid.uuid4(),
            created_by_user_id=owner.id,
            user_input="hello",
            context=None,
            max_attempts=3,
        )
    result = await db_session.execute(select(Job))
    assert result.scalars().all() == []


# --- enqueue_workflow_run ------------------------------------------------


async def test_enqueue_workflow_run_creates_queued_run_and_queued_job(
    db_session, owner, make_agent_definition, make_workflow_definition
) -> None:
    agent = await make_agent_definition()
    steps = [{"step_id": "only", "name": "Only", "agent_definition_id": str(agent.id), "order": 1}]
    workflow = await make_workflow_definition(steps=steps)

    run, job = await job_service.enqueue_workflow_run(
        db_session,
        workflow_id=workflow.id,
        created_by_user_id=owner.id,
        user_input="hello",
        context=None,
        max_attempts=3,
    )
    assert run.status == WorkflowRunStatus.QUEUED
    assert job.status == JobStatus.QUEUED
    assert job.job_type == job_service.WORKFLOW_RUN_JOB_TYPE
    assert job.target_run_id == run.id


async def test_enqueue_workflow_run_with_empty_steps_raises_and_creates_nothing(
    db_session, owner, make_workflow_definition
) -> None:
    workflow = await make_workflow_definition(steps=[])
    with pytest.raises(WorkflowDomainError):
        await job_service.enqueue_workflow_run(
            db_session,
            workflow_id=workflow.id,
            created_by_user_id=owner.id,
            user_input="hello",
            context=None,
            max_attempts=3,
        )
    result = await db_session.execute(select(Job))
    assert result.scalars().all() == []


# --- cancel_agent_run: three-tier design ----------------------------------


async def test_cancel_agent_run_queued_cancels_both_job_and_run(
    db_session, owner, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    run, job = await job_service.enqueue_agent_run(
        db_session,
        agent_id=agent.id,
        created_by_user_id=owner.id,
        user_input="hi",
        context=None,
        max_attempts=3,
    )
    result = await job_service.cancel_agent_run(db_session, run.id, owner)
    assert result.status == AgentRunStatus.CANCELLED
    await db_session.refresh(job)
    assert job.status == JobStatus.CANCELLED


async def test_cancel_agent_run_running_job_raises_job_already_running(
    db_session, owner, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    run, job = await job_service.enqueue_agent_run(
        db_session,
        agent_id=agent.id,
        created_by_user_id=owner.id,
        user_input="hi",
        context=None,
        max_attempts=3,
    )
    await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=120.0)

    with pytest.raises(JobAlreadyRunningError):
        await job_service.cancel_agent_run(db_session, run.id, owner)


async def test_cancel_agent_run_unknown_run_raises_not_found(db_session, owner) -> None:
    with pytest.raises(AgentRunNotFoundError):
        await job_service.cancel_agent_run(db_session, uuid.uuid4(), owner)


async def test_cancel_agent_run_non_owner_raises_not_found(
    db_session, owner, other_user, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    run, _job = await job_service.enqueue_agent_run(
        db_session,
        agent_id=agent.id,
        created_by_user_id=owner.id,
        user_input="hi",
        context=None,
        max_attempts=3,
    )
    with pytest.raises(AgentRunNotFoundError):
        await job_service.cancel_agent_run(db_session, run.id, other_user)


async def test_cancel_agent_run_no_paired_job_falls_through_to_run_level_cancel(
    db_session, owner, make_agent_definition
) -> None:
    """A run constructed directly via the ORM (as several P6 tests do,
    bypassing enqueue_agent_run entirely) has no Job row at all - cancel
    must still work, delegating to the unchanged P6 run-level cancel."""
    from app.models.agent_run import AgentRun

    agent = await make_agent_definition()
    run = AgentRun(agent_id=agent.id, created_by_user_id=owner.id, status=AgentRunStatus.QUEUED)
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    result = await job_service.cancel_agent_run(db_session, run.id, owner)
    assert result.status == AgentRunStatus.CANCELLED


# --- cancel_workflow_run: three-tier design (RUNNING case differs) -------


async def test_cancel_workflow_run_queued_cancels_both_job_and_run(
    db_session, owner, make_agent_definition, make_workflow_definition
) -> None:
    agent = await make_agent_definition()
    steps = [{"step_id": "only", "name": "Only", "agent_definition_id": str(agent.id), "order": 1}]
    workflow = await make_workflow_definition(steps=steps)
    run, job = await job_service.enqueue_workflow_run(
        db_session,
        workflow_id=workflow.id,
        created_by_user_id=owner.id,
        user_input="hi",
        context=None,
        max_attempts=3,
    )

    result = await job_service.cancel_workflow_run(db_session, run.id, owner)
    assert result.status == WorkflowRunStatus.CANCELLED
    await db_session.refresh(job)
    assert job.status == JobStatus.CANCELLED


async def test_cancel_workflow_run_running_job_sets_cancel_requested_at_not_cancelled(
    db_session, owner, make_agent_definition, make_workflow_definition
) -> None:
    """Unlike an agent-run job, a RUNNING workflow-run job is not
    rejected with JobAlreadyRunningError - a cooperative
    cancel_requested_at flag is set instead, and the run itself is
    returned unchanged (still RUNNING), since the worker checks this flag
    between steps rather than the API interrupting it directly."""
    agent = await make_agent_definition()
    steps = [{"step_id": "only", "name": "Only", "agent_definition_id": str(agent.id), "order": 1}]
    workflow = await make_workflow_definition(steps=steps)
    run, job = await job_service.enqueue_workflow_run(
        db_session,
        workflow_id=workflow.id,
        created_by_user_id=owner.id,
        user_input="hi",
        context=None,
        max_attempts=3,
    )
    await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=120.0)

    result = await job_service.cancel_workflow_run(db_session, run.id, owner)

    assert result.status == WorkflowRunStatus.QUEUED  # unchanged by this call; worker owns RUNNING
    await db_session.refresh(job)
    assert job.status == JobStatus.RUNNING
    assert job.cancel_requested_at is not None


async def test_cancel_workflow_run_running_job_cancel_request_is_idempotent(
    db_session, owner, make_agent_definition, make_workflow_definition
) -> None:
    agent = await make_agent_definition()
    steps = [{"step_id": "only", "name": "Only", "agent_definition_id": str(agent.id), "order": 1}]
    workflow = await make_workflow_definition(steps=steps)
    run, job = await job_service.enqueue_workflow_run(
        db_session,
        workflow_id=workflow.id,
        created_by_user_id=owner.id,
        user_input="hi",
        context=None,
        max_attempts=3,
    )
    await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=120.0)

    await job_service.cancel_workflow_run(db_session, run.id, owner)
    await db_session.refresh(job)
    first_requested_at = job.cancel_requested_at

    await job_service.cancel_workflow_run(db_session, run.id, owner)
    await db_session.refresh(job)

    assert job.cancel_requested_at == first_requested_at


async def test_cancel_workflow_run_unknown_run_raises_not_found(db_session, owner) -> None:
    with pytest.raises(WorkflowRunNotFoundError):
        await job_service.cancel_workflow_run(db_session, uuid.uuid4(), owner)


# --- enqueue_asset_ingestion (Sprint P10B3) -------------------------------


async def test_enqueue_asset_ingestion_creates_queued_job(db_session, make_asset) -> None:
    asset = await make_asset(content=b"hello world", content_type="text/plain")

    job = await job_service.enqueue_asset_ingestion(
        db_session,
        asset_id=asset.id,
        embedding_model="fake-embed-v1",
        embedding_provider="fake",
        chunk_size=500,
        chunk_overlap=50,
        max_attempts=3,
    )

    assert job.status == JobStatus.QUEUED
    assert job.job_type == job_service.ASSET_INGESTION_JOB_TYPE
    assert job.target_run_id == asset.id
    assert job.input_json == {
        "embedding_model": "fake-embed-v1",
        "embedding_provider": "fake",
        "chunk_size": 500,
        "chunk_overlap": 50,
    }
    assert job.max_attempts == 3


async def test_enqueue_asset_ingestion_persists_the_job(db_session, make_asset) -> None:
    asset = await make_asset()
    job = await job_service.enqueue_asset_ingestion(
        db_session,
        asset_id=asset.id,
        embedding_model="fake-embed-v1",
        embedding_provider=None,
        chunk_size=1000,
        chunk_overlap=200,
        max_attempts=3,
    )
    result = await db_session.execute(select(Job).where(Job.id == job.id))
    assert result.scalar_one().target_run_id == asset.id


async def test_enqueue_asset_ingestion_unknown_asset_raises_and_creates_nothing(
    db_session,
) -> None:
    with pytest.raises(AssetNotFoundError):
        await job_service.enqueue_asset_ingestion(
            db_session,
            asset_id=uuid.uuid4(),
            embedding_model="fake-embed-v1",
            embedding_provider=None,
            chunk_size=1000,
            chunk_overlap=200,
            max_attempts=3,
        )
    result = await db_session.execute(select(Job).where(Job.job_type == "asset_ingestion"))
    assert result.scalars().all() == []


async def test_enqueue_asset_ingestion_unsupported_content_type_raises(
    db_session, make_asset
) -> None:
    asset = await make_asset(content=b"\x89PNG", content_type="image/png")
    with pytest.raises(AssetNotIngestableError):
        await job_service.enqueue_asset_ingestion(
            db_session,
            asset_id=asset.id,
            embedding_model="fake-embed-v1",
            embedding_provider=None,
            chunk_size=1000,
            chunk_overlap=200,
            max_attempts=3,
        )


async def test_enqueue_asset_ingestion_already_ingested_raises(
    db_session, make_asset, make_document_chunk
) -> None:
    asset = await make_asset()
    await make_document_chunk(asset_id=asset.id)
    with pytest.raises(AssetAlreadyIngestedError):
        await job_service.enqueue_asset_ingestion(
            db_session,
            asset_id=asset.id,
            embedding_model="fake-embed-v1",
            embedding_provider=None,
            chunk_size=1000,
            chunk_overlap=200,
            max_attempts=3,
        )


async def test_enqueue_asset_ingestion_second_concurrent_request_is_rejected(
    db_session, make_asset
) -> None:
    """The real guarantee, not just the upfront check: check_ingestable()
    only looks at document_chunks (empty here, since the first job
    hasn't run yet), so nothing at the application level stops a second
    enqueue call for the same asset while the first is still QUEUED -
    app/models/job.py's uq_jobs_active_asset_ingestion partial unique
    index is what actually rejects it, via the IntegrityError this
    function catches and turns into AssetIngestionAlreadyQueuedError.

    Captures asset.id/first_job.id before the second call - that call's
    own IntegrityError handling does a real db.rollback(), which expires
    every object already loaded in this session (unlike commit(), which
    expire_on_commit=False only suppresses for commits); touching the
    ORM objects again afterward without re-fetching would otherwise
    itself raise (MissingGreenlet, from an implicit lazy-reload
    happening outside an awaited context)."""
    asset = await make_asset()
    asset_id = asset.id
    first_job = await job_service.enqueue_asset_ingestion(
        db_session,
        asset_id=asset_id,
        embedding_model="fake-embed-v1",
        embedding_provider=None,
        chunk_size=1000,
        chunk_overlap=200,
        max_attempts=3,
    )
    assert first_job.status == JobStatus.QUEUED
    first_job_id = first_job.id

    with pytest.raises(AssetIngestionAlreadyQueuedError):
        await job_service.enqueue_asset_ingestion(
            db_session,
            asset_id=asset_id,
            embedding_model="fake-embed-v1",
            embedding_provider=None,
            chunk_size=1000,
            chunk_overlap=200,
            max_attempts=3,
        )

    result = await db_session.execute(
        select(Job).where(Job.job_type == "asset_ingestion", Job.target_run_id == asset_id)
    )
    # The rejected second attempt must not have left a row behind either.
    assert [j.id for j in result.scalars().all()] == [first_job_id]


async def test_enqueue_asset_ingestion_allowed_again_once_prior_job_is_terminal(
    db_session, make_asset
) -> None:
    """uq_jobs_active_asset_ingestion is scoped to QUEUED/RUNNING/
    RETRYING - once the earlier job reaches FAILED, a new ingestion
    attempt for the same asset must be enqueueable again."""
    asset = await make_asset()
    first_job = await job_service.enqueue_asset_ingestion(
        db_session,
        asset_id=asset.id,
        embedding_model="fake-embed-v1",
        embedding_provider=None,
        chunk_size=1000,
        chunk_overlap=200,
        max_attempts=3,
    )
    # mark_failed requires RUNNING (see app/jobs/models.py's transition
    # table) - claim it first, same as a real worker would.
    claimed = await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=120.0)
    assert claimed is not None and claimed.id == first_job.id
    await queue.mark_failed(db_session, claimed, error_code="Boom", error_message="boom")

    second_job = await job_service.enqueue_asset_ingestion(
        db_session,
        asset_id=asset.id,
        embedding_model="fake-embed-v1",
        embedding_provider=None,
        chunk_size=1000,
        chunk_overlap=200,
        max_attempts=3,
    )
    assert second_job.id != first_job.id
    assert second_job.status == JobStatus.QUEUED


# --- uq_jobs_active_asset_ingestion: the raw database-level guarantee ----


async def test_partial_unique_index_rejects_two_active_ingestion_jobs_for_same_asset(
    db_session, make_job
) -> None:
    """Direct ORM-level proof the constraint itself (not just
    enqueue_asset_ingestion's IntegrityError handling above) is what
    closes the race - two non-terminal asset_ingestion Job rows
    targeting the same asset_id can never both exist."""
    target = uuid.uuid4()
    await make_job(job_type="asset_ingestion", status=JobStatus.QUEUED, target_run_id=target)

    with pytest.raises(IntegrityError):
        await make_job(job_type="asset_ingestion", status=JobStatus.QUEUED, target_run_id=target)


async def test_partial_unique_index_does_not_constrain_other_job_types(
    db_session, make_job
) -> None:
    """The index's WHERE clause is scoped to job_type = 'asset_ingestion'
    - two QUEUED agent_run jobs sharing a target_run_id (however
    unlikely in practice) must not be rejected by this index."""
    target = uuid.uuid4()
    await make_job(job_type="agent_run", status=JobStatus.QUEUED, target_run_id=target)
    # Must not raise.
    await make_job(job_type="agent_run", status=JobStatus.QUEUED, target_run_id=target)


async def test_partial_unique_index_allows_new_job_once_prior_is_terminal(
    db_session, make_job
) -> None:
    target = uuid.uuid4()
    await make_job(job_type="asset_ingestion", status=JobStatus.COMPLETED, target_run_id=target)
    # Must not raise - COMPLETED is outside the index's WHERE clause.
    await make_job(job_type="asset_ingestion", status=JobStatus.QUEUED, target_run_id=target)


# --- get_latest_asset_ingestion_job ---------------------------------------


async def test_get_latest_asset_ingestion_job_returns_none_when_never_enqueued(
    db_session, make_asset
) -> None:
    asset = await make_asset()
    assert await job_service.get_latest_asset_ingestion_job(db_session, asset.id) is None


async def test_get_latest_asset_ingestion_job_returns_the_newest(
    db_session, make_asset, make_job
) -> None:
    """Built directly via make_job with explicit, distinct created_at
    values (same convention as tests/test_job_queue.py's
    test_claim_next_job_claims_oldest_queued_job) rather than two real
    enqueue_asset_ingestion calls - both would run inside this test's
    single outer database transaction (see conftest.py's db_session
    fixture), and Postgres's now() is constant for the lifetime of one
    transaction, so their created_at timestamps would tie and the
    "newest" assertion below would be timing-dependent rather than
    deterministic."""
    from datetime import datetime, timedelta, timezone

    asset = await make_asset()
    now = datetime.now(timezone.utc)
    older_job = await make_job(
        job_type="asset_ingestion",
        status=JobStatus.FAILED,
        target_run_id=asset.id,
        created_at=now - timedelta(minutes=5),
    )
    newer_job = await make_job(
        job_type="asset_ingestion",
        status=JobStatus.QUEUED,
        target_run_id=asset.id,
        created_at=now,
    )
    assert older_job.id != newer_job.id

    latest = await job_service.get_latest_asset_ingestion_job(db_session, asset.id)
    assert latest is not None
    assert latest.id == newer_job.id
