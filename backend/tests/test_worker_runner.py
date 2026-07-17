"""Tests for app/worker/runner.py (Sprint P8): the poll loop, concurrent
lease renewal during long-running jobs, worker heartbeat upserts, and
startup stale-job recovery.

Uses the `worker_session_factory` fixture (see tests/conftest.py), not
the shared `db_session` fixture - the runner legitimately opens many
independent sessions over its lifetime (one per claim, one per heartbeat
tick, ...), which a single already-instantiated Session object cannot
represent (AsyncSession.__aexit__ closes it). Test data is therefore set
up directly through worker_session_factory too, with real commits - safe
because test_engine (which it's bound to) creates and drops a fully
isolated schema per test.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import hash_password
from app.jobs import queue as job_queue
from app.jobs import service as job_service
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeProvider
from app.models.agent_definition import AgentDefinition
from app.models.agent_run import AgentRun
from app.models.enums import AgentRunStatus, AgentStatus, JobStatus, UserRole, UserStatus
from app.models.job import Job
from app.models.user import User
from app.models.worker_heartbeat import WorkerHeartbeat
from app.worker import runner


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "jwt_secret_key": "x" * 32,
        "llm_allowed_providers": "fake",
        "llm_default_provider": "fake",
        "job_retry_backoff_base_seconds": 0.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _gateway(settings: Settings, provider: FakeProvider | None = None) -> LLMGateway:
    return LLMGateway(
        {"fake": provider or FakeProvider()}, settings, retry_base_delay_seconds=0.0
    )


async def _make_user(session: AsyncSession) -> User:
    user = User(
        email=f"user-{uuid.uuid4().hex[:10]}@example.com",
        hashed_password=hash_password("a-perfectly-fine-passw0rd"),
        full_name="Test User",
        role=UserRole.OPERATOR,
        status=UserStatus.ACTIVE,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_agent(session: AsyncSession) -> AgentDefinition:
    agent = AgentDefinition(
        name=f"agent-{uuid.uuid4().hex[:10]}",
        role="Test Agent",
        status=AgentStatus.ACTIVE,
        configuration_json={"system_prompt": "You are a helpful test agent.", "model": "fake-v1"},
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


async def _enqueue_agent_run(session: AsyncSession) -> uuid.UUID:
    user = await _make_user(session)
    agent = await _make_agent(session)
    run, _job = await job_service.enqueue_agent_run(
        session,
        agent_id=agent.id,
        created_by_user_id=user.id,
        user_input="hello",
        context=None,
        max_attempts=3,
    )
    return run.id


# --- default_worker_id -----------------------------------------------


def test_default_worker_id_uses_env_var_when_set(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_ID", "explicit-worker-id")
    assert runner.default_worker_id() == "explicit-worker-id"


def test_default_worker_id_falls_back_to_hostname(monkeypatch) -> None:
    monkeypatch.delenv("WORKER_ID", raising=False)
    assert runner.default_worker_id() == socket.gethostname()


# --- upsert_heartbeat ---------------------------------------------------


async def test_upsert_heartbeat_inserts_then_updates(worker_session_factory) -> None:
    started_at = datetime.now(timezone.utc)
    async with worker_session_factory() as db:
        await runner.upsert_heartbeat(db, "w-upsert-test", started_at=started_at)
    async with worker_session_factory() as db:
        first = await db.get(WorkerHeartbeat, "w-upsert-test")
        assert first is not None
        first_seen = first.last_heartbeat_at

    async with worker_session_factory() as db:
        await runner.upsert_heartbeat(db, "w-upsert-test", started_at=started_at)
    async with worker_session_factory() as db:
        second = await db.get(WorkerHeartbeat, "w-upsert-test")
        assert second is not None
        assert second.last_heartbeat_at >= first_seen
        assert second.started_at.replace(microsecond=0) == first.started_at.replace(microsecond=0)


# --- process_one_job -------------------------------------------------------


async def test_process_one_job_returns_false_when_queue_empty(worker_session_factory) -> None:
    settings = _settings()
    gateway = _gateway(settings)
    did_work = await runner.process_one_job(
        worker_session_factory, gateway, settings, worker_id="w1"
    )
    assert did_work is False


async def test_process_one_job_claims_and_completes_a_real_job(worker_session_factory) -> None:
    async with worker_session_factory() as setup_db:
        run_id = await _enqueue_agent_run(setup_db)

    settings = _settings()
    gateway = _gateway(settings, FakeProvider(response_content="hi there"))

    did_work = await runner.process_one_job(
        worker_session_factory, gateway, settings, worker_id="w1"
    )
    assert did_work is True

    async with worker_session_factory() as check_db:
        run = await check_db.get(AgentRun, run_id)
        assert run is not None
        assert run.status == AgentRunStatus.COMPLETED
        result = await check_db.execute(select(Job).where(Job.target_run_id == run_id))
        job = result.scalar_one()
        assert job.status == JobStatus.COMPLETED


# --- _heartbeat_while_running: the lease-renewal mechanism ----------------


async def test_heartbeat_while_running_extends_the_lease(worker_session_factory) -> None:
    async with worker_session_factory() as setup_db:
        await _enqueue_agent_run(setup_db)

    async with worker_session_factory() as claim_db:
        claimed = await job_queue.claim_next_job(claim_db, worker_id="w1", lease_seconds=1.0)
        assert claimed is not None
        job_id = claimed.id
        initial_lease = claimed.lease_expires_at

    task = asyncio.create_task(
        runner._heartbeat_while_running(
            worker_session_factory, job_id=job_id, lease_seconds=10.0, interval_seconds=0.05
        )
    )
    await asyncio.sleep(0.18)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    async with worker_session_factory() as check_db:
        job = await check_db.get(Job, job_id)
        assert job is not None
        assert job.lease_expires_at > initial_lease


async def test_heartbeat_while_running_stops_once_job_is_no_longer_running(
    worker_session_factory,
) -> None:
    """Once the job has already been marked terminal by whatever claimed
    it, the heartbeat loop must notice and stop renewing - a completed
    job's lease is meaningless and should not keep being extended."""
    async with worker_session_factory() as setup_db:
        await _enqueue_agent_run(setup_db)

    async with worker_session_factory() as claim_db:
        claimed = await job_queue.claim_next_job(claim_db, worker_id="w1", lease_seconds=1.0)
        assert claimed is not None
        job_id = claimed.id
        await job_queue.mark_completed(claim_db, claimed)

    task = asyncio.create_task(
        runner._heartbeat_while_running(
            worker_session_factory, job_id=job_id, lease_seconds=10.0, interval_seconds=0.05
        )
    )
    await asyncio.sleep(0.12)
    # The loop should have returned on its own (job is COMPLETED, not
    # RUNNING) well before this - not still running and needing a cancel.
    assert task.done()


# --- run_worker: startup stale-job recovery + heartbeat -------------------


async def test_run_worker_recovers_stale_jobs_on_startup(worker_session_factory) -> None:
    async with worker_session_factory() as setup_db:
        run_id = await _enqueue_agent_run(setup_db)
        result = await setup_db.execute(select(Job).where(Job.target_run_id == run_id))
        job_row = result.scalar_one()
        job_row.status = JobStatus.RUNNING
        job_row.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        job_row.attempt_count = 1
        job_row.max_attempts = 3
        setup_db.add(job_row)
        await setup_db.commit()
        job_id = job_row.id

    settings = _settings()
    gateway = _gateway(settings)
    shutdown_event = asyncio.Event()
    shutdown_event.set()  # loop body never runs; only the startup recovery pass does

    await runner.run_worker(
        worker_id="w-recovery-test",
        session_factory=worker_session_factory,
        gateway=gateway,
        settings=settings,
        shutdown_event=shutdown_event,
    )

    async with worker_session_factory() as check_db:
        job = await check_db.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.RETRYING


async def test_run_worker_creates_worker_heartbeat_row_on_startup(worker_session_factory) -> None:
    settings = _settings()
    gateway = _gateway(settings)
    shutdown_event = asyncio.Event()
    shutdown_event.set()

    await runner.run_worker(
        worker_id="w-heartbeat-startup-test",
        session_factory=worker_session_factory,
        gateway=gateway,
        settings=settings,
        shutdown_event=shutdown_event,
    )

    async with worker_session_factory() as check_db:
        heartbeat = await check_db.get(WorkerHeartbeat, "w-heartbeat-startup-test")
        assert heartbeat is not None
        assert heartbeat.last_heartbeat_at is not None
