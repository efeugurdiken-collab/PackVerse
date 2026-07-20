"""Tests for app/worker/dispatch.py (Sprint P8): mapping a claimed Job to
the correct P6/P7 executor call, retry-vs-terminal-failure policy,
duplicate-delivery handling, and completed-run replay prevention. Sprint
P10B3 adds the asset_ingestion branch (_process_asset_ingestion_job),
covered in its own section below.

Every test routes through the network-free "fake" LLM provider, exactly
mirroring tests/test_runtime_executor.py's/test_workflow_executor.py's
approach.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.jobs import queue, service as job_service
from app.llm.exceptions import LLMRateLimitError
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeProvider
from app.models.document_chunk import DocumentChunk
from app.models.enums import AgentRunStatus, JobStatus, UserRole, WorkflowRunStatus
from app.runtime.exceptions import AgentConfigurationError
from app.worker import dispatch


def _settings(**overrides: object) -> Settings:
    return Settings(
        jwt_secret_key="x" * 32,
        llm_allowed_providers="fake",
        llm_default_provider="fake",
        job_retry_backoff_base_seconds=0.0,
        **overrides,
    )


def _gateway(provider: FakeProvider, settings: Settings) -> LLMGateway:
    return LLMGateway({"fake": provider}, settings, retry_base_delay_seconds=0.0)


@pytest.fixture
async def owner(make_user):
    return await make_user(role=UserRole.OPERATOR)


async def _enqueue_and_claim_agent_run(db_session, owner, make_agent_definition, **agent_kwargs):
    agent = await make_agent_definition(**agent_kwargs)
    run, _job = await job_service.enqueue_agent_run(
        db_session,
        agent_id=agent.id,
        created_by_user_id=owner.id,
        user_input="hello",
        context=None,
        max_attempts=3,
    )
    claimed = await queue.claim_next_job(db_session, worker_id="test-worker", lease_seconds=120.0)
    assert claimed is not None
    return run, claimed


async def _enqueue_and_claim_workflow_run(
    db_session, owner, make_agent_definition, make_workflow_definition
):
    agent = await make_agent_definition()
    steps = [{"step_id": "only", "name": "Only", "agent_definition_id": str(agent.id), "order": 1}]
    workflow = await make_workflow_definition(steps=steps)
    run, _job = await job_service.enqueue_workflow_run(
        db_session,
        workflow_id=workflow.id,
        created_by_user_id=owner.id,
        user_input="hello",
        context=None,
        max_attempts=3,
    )
    claimed = await queue.claim_next_job(db_session, worker_id="test-worker", lease_seconds=120.0)
    assert claimed is not None
    return run, claimed


# --- agent-run jobs: success reuses the P6 executor -----------------------


async def test_agent_job_success_completes_job_and_run(
    db_session, owner, make_agent_definition, storage_backend
) -> None:
    run, job = await _enqueue_and_claim_agent_run(db_session, owner, make_agent_definition)
    settings = _settings()
    gateway = _gateway(FakeProvider(response_content="the answer"), settings)

    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    await db_session.refresh(job)
    await db_session.refresh(run)
    assert job.status == JobStatus.COMPLETED
    assert run.status == AgentRunStatus.COMPLETED
    assert run.output_text == "the answer"
    # Proof of reuse, not reimplementation: provider/model/token fields
    # only ever get set by app.runtime.executor.execute_run's own
    # success path (see tests/test_runtime_executor.py).
    assert run.provider == "fake"
    assert run.total_tokens is not None


# --- agent-run jobs: domain failures never retry ---------------------------


async def test_agent_job_llm_error_fails_job_immediately_no_retry(
    db_session, owner, make_agent_definition, storage_backend
) -> None:
    run, job = await _enqueue_and_claim_agent_run(db_session, owner, make_agent_definition)
    settings = _settings()
    gateway = _gateway(FakeProvider(fail_with=LLMRateLimitError("fake")), settings)

    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    await db_session.refresh(job)
    await db_session.refresh(run)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "LLMRateLimitError"
    assert run.status == AgentRunStatus.FAILED


async def test_agent_job_misconfigured_agent_fails_job_immediately_no_retry(
    db_session, owner, make_agent_definition, storage_backend
) -> None:
    run, job = await _enqueue_and_claim_agent_run(
        db_session, owner, make_agent_definition, configuration_json={"model": "fake-v1"}
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    await db_session.refresh(job)
    await db_session.refresh(run)
    assert job.status == JobStatus.FAILED
    assert job.error_code == AgentConfigurationError.__name__
    assert run.status == AgentRunStatus.FAILED


# --- agent-run jobs: genuinely unexpected errors DO retry -----------------


async def test_agent_job_unexpected_error_retries_when_attempts_remain(
    db_session, owner, make_agent_definition, monkeypatch, storage_backend
) -> None:
    run, job = await _enqueue_and_claim_agent_run(db_session, owner, make_agent_definition)
    assert job.attempt_count == 1
    assert job.max_attempts == 3
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("infra hiccup")

    monkeypatch.setattr(dispatch, "execute_run", _boom)

    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    await db_session.refresh(job)
    assert job.status == JobStatus.RETRYING
    assert job.error_code == "RuntimeError"
    assert job.next_attempt_at is not None


async def test_agent_job_unexpected_error_fails_once_attempts_exhausted(
    db_session, owner, make_agent_definition, make_job, monkeypatch, storage_backend
) -> None:
    agent = await make_agent_definition()
    run, _job = await job_service.enqueue_agent_run(
        db_session,
        agent_id=agent.id,
        created_by_user_id=owner.id,
        user_input="hello",
        context=None,
        max_attempts=1,
    )
    claimed = await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=120.0)
    assert claimed is not None
    assert claimed.attempt_count == 1
    assert claimed.max_attempts == 1

    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("infra hiccup")

    monkeypatch.setattr(dispatch, "execute_run", _boom)

    await dispatch.process_claimed_job(db_session, gateway, settings, job=claimed, storage=storage_backend)

    await db_session.refresh(claimed)
    assert claimed.status == JobStatus.FAILED


# --- agent-run jobs: duplicate delivery / completed-run replay prevention --


async def test_agent_job_duplicate_delivery_run_already_completed_does_not_reexecute(
    db_session, owner, make_agent_definition, monkeypatch, storage_backend
) -> None:
    """Simulates a duplicate delivery: the run has already been completed
    (e.g. by an earlier, still-in-flight worker attempt) by the time this
    process_claimed_job call looks at it. execute_run must never be
    called again."""
    run, job = await _enqueue_and_claim_agent_run(db_session, owner, make_agent_definition)
    run.status = AgentRunStatus.COMPLETED
    db_session.add(run)
    await db_session.commit()

    called = False

    async def _should_not_be_called(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(dispatch, "execute_run", _should_not_be_called)

    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)
    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    assert called is False
    await db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


async def test_agent_job_target_run_already_cancelled_marks_job_cancelled(
    db_session, owner, make_agent_definition, storage_backend
) -> None:
    run, job = await _enqueue_and_claim_agent_run(db_session, owner, make_agent_definition)
    run.status = AgentRunStatus.CANCELLED
    db_session.add(run)
    await db_session.commit()

    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)
    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    await db_session.refresh(job)
    assert job.status == JobStatus.CANCELLED


# --- workflow-run jobs: success reuses the P7 executor ---------------------


async def test_workflow_job_success_completes_job_and_run(
    db_session, owner, make_agent_definition, make_workflow_definition, storage_backend
) -> None:
    run, job = await _enqueue_and_claim_workflow_run(
        db_session, owner, make_agent_definition, make_workflow_definition
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(response_content="step output"), settings)

    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    await db_session.refresh(job)
    await db_session.refresh(run)
    assert job.status == JobStatus.COMPLETED
    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.output_text == "step output"


# --- workflow-run jobs: cooperative cancellation between steps ------------


async def test_workflow_job_honors_cancel_requested_at_between_steps(
    db_session, owner, make_agent_definition, make_workflow_definition, storage_backend
) -> None:
    """A cancel_requested_at set on the Job (by app/jobs/service.py's
    cancel_workflow_run, simulated directly here) must be honored the
    next time the worker's cancellation_check callback is polled - since
    this workflow has only one step, the check that matters is the one
    evaluated before that first step runs."""
    from datetime import datetime, timezone

    run, job = await _enqueue_and_claim_workflow_run(
        db_session, owner, make_agent_definition, make_workflow_definition
    )
    job.cancel_requested_at = datetime.now(timezone.utc)
    db_session.add(job)
    await db_session.commit()

    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)
    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    await db_session.refresh(job)
    await db_session.refresh(run)
    assert job.status == JobStatus.CANCELLED
    assert run.status == WorkflowRunStatus.CANCELLED


async def test_workflow_job_domain_error_fails_job_no_retry(
    db_session, owner, make_agent_definition, make_workflow_definition, storage_backend
) -> None:
    run, job = await _enqueue_and_claim_workflow_run(
        db_session, owner, make_agent_definition, make_workflow_definition
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(fail_with=LLMRateLimitError("fake")), settings)

    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    await db_session.refresh(job)
    await db_session.refresh(run)
    assert job.status == JobStatus.FAILED
    assert run.status == WorkflowRunStatus.FAILED


# --- asset_ingestion jobs (Sprint P10B3): reuses ingest_asset() -----------


async def _enqueue_and_claim_asset_ingestion(db_session, make_asset, **asset_kwargs):
    asset = await make_asset(**asset_kwargs)
    await job_service.enqueue_asset_ingestion(
        db_session,
        asset_id=asset.id,
        embedding_model="fake-embed-v1",
        embedding_provider="fake",
        chunk_size=1000,
        chunk_overlap=200,
        max_attempts=3,
    )
    claimed = await queue.claim_next_job(db_session, worker_id="test-worker", lease_seconds=120.0)
    assert claimed is not None
    return asset, claimed


async def test_ingestion_job_success_completes_job_and_persists_chunks(
    db_session, make_asset, storage_backend
) -> None:
    asset, job = await _enqueue_and_claim_asset_ingestion(
        db_session, make_asset, content=b"hello world, this is a real document.",
        content_type="text/plain",
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    await db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED
    result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.asset_id == asset.id)
    )
    chunks = result.scalars().all()
    assert len(chunks) == 1
    assert chunks[0].embedding_model == "fake-embed-v1"
    assert chunks[0].embedding_provider == "fake"


async def test_ingestion_job_llm_error_fails_job_immediately_no_retry(
    db_session, make_asset, storage_backend
) -> None:
    asset, job = await _enqueue_and_claim_asset_ingestion(
        db_session, make_asset, content=b"hello world", content_type="text/plain"
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(fail_with=LLMRateLimitError("fake")), settings)

    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    await db_session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "LLMRateLimitError"
    result = await db_session.execute(
        select(DocumentChunk).where(DocumentChunk.asset_id == asset.id)
    )
    assert result.scalars().all() == []


async def test_ingestion_job_domain_error_fails_job_immediately_no_retry(
    db_session, make_asset, storage_backend
) -> None:
    """check_ingestable() only rejects an unsupported content type or an
    already-ingested asset at enqueue time - it does not (and cannot)
    guarantee extraction will produce non-whitespace text, so
    EmptyExtractedTextError is a domain error that's only discoverable
    once the worker actually reads/extracts the content. Must still
    fail the job immediately, never retry."""
    asset, job = await _enqueue_and_claim_asset_ingestion(
        db_session, make_asset, content=b"   \n\t  ", content_type="text/plain"
    )
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    await db_session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "EmptyExtractedTextError"


async def test_ingestion_job_unexpected_error_retries_when_attempts_remain(
    db_session, make_asset, storage_backend, monkeypatch
) -> None:
    asset, job = await _enqueue_and_claim_asset_ingestion(
        db_session, make_asset, content=b"hello world", content_type="text/plain"
    )
    assert job.attempt_count == 1
    assert job.max_attempts == 3
    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("infra hiccup")

    monkeypatch.setattr(dispatch, "ingest_asset", _boom)

    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    await db_session.refresh(job)
    assert job.status == JobStatus.RETRYING
    assert job.error_code == "RuntimeError"
    assert job.next_attempt_at is not None


async def test_ingestion_job_duplicate_delivery_chunks_already_exist_does_not_reembed(
    db_session, make_asset, make_document_chunk, storage_backend, monkeypatch
) -> None:
    """Simulates a duplicate delivery: document_chunks already exist for
    this asset (e.g. an earlier, still-in-flight worker attempt already
    finished) by the time this process_claimed_job call looks at it.
    ingest_asset() must never be called again - it would only fail with
    AssetAlreadyIngestedError anyway, wasting an embedding call first."""
    asset, job = await _enqueue_and_claim_asset_ingestion(
        db_session, make_asset, content=b"hello world", content_type="text/plain"
    )
    await make_document_chunk(asset_id=asset.id)

    called = False

    async def _should_not_be_called(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(dispatch, "ingest_asset", _should_not_be_called)

    settings = _settings()
    gateway = _gateway(FakeProvider(), settings)
    await dispatch.process_claimed_job(db_session, gateway, settings, job=job, storage=storage_backend)

    assert called is False
    await db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED
