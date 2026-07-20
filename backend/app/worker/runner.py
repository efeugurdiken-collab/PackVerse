"""Worker poll loop (Sprint P8).

`run_worker` is the process's main loop: recover any stale jobs left
over from a previous crash, then repeatedly claim and execute one job at
a time (sleeping between polls when the queue is empty), until
`shutdown_event` is set. `process_one_job` (claim + dispatch exactly one
job, returning whether it did anything) is the independently-testable
primitive tests call directly, the same "one-shot primitive vs. infinite
loop" split app/runtime/executor.py and app/workflows/executor.py
already established for their own execute_* functions.

Lease renewal while a job is actively executing: app.runtime.executor.
execute_run and app.workflows.executor.execute_workflow_run are both
long, mostly-synchronous calls (each with many internal `await` points -
LLM Gateway calls, db.commit()s - but no natural hook for this module to
inject a periodic action into their own control flow, short of the
cancellation_check parameter Sprint P8 already added to the latter for a
different purpose). A long-running job could otherwise outlive its own
lease (job_lease_seconds) while a worker is still legitimately working
on it, causing recover_stale_jobs to wrongly reclaim live work. Solved
here by running a small background task (_heartbeat_while_running)
concurrently with the executor call, on its own short-lived session
(AsyncSession is not safe for concurrent use from two coroutines at
once) - it wakes up every job_heartbeat_interval_seconds and renews the
lease, and is cancelled the moment the job finishes.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.jobs import queue
from app.llm.gateway import LLMGateway
from app.models.enums import JobStatus
from app.models.job import Job
from app.models.worker_heartbeat import WorkerHeartbeat
from app.storage.base import StorageBackend
from app.worker.dispatch import process_claimed_job

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]


def default_worker_id() -> str:
    """WORKER_ID env var if set, else the process hostname (inside
    Docker, the container id). Assumes at most one worker process per
    hostname - running multiple worker processes on the same host
    outside Docker Compose requires setting WORKER_ID explicitly per
    process, or worker_heartbeats rows (and job leases) from different
    processes will collide. See the Sprint P8 report's Known
    Limitations."""
    return os.environ.get("WORKER_ID") or socket.gethostname()


async def upsert_heartbeat(
    db: AsyncSession, worker_id: str, *, started_at: datetime
) -> None:
    """Records that `worker_id` is alive right now - see
    app/models/worker_heartbeat.py's module docstring for why this is
    separate from a job's own heartbeat_at."""
    now = datetime.now(timezone.utc)
    existing = await db.get(WorkerHeartbeat, worker_id)
    if existing is None:
        db.add(WorkerHeartbeat(worker_id=worker_id, started_at=started_at, last_heartbeat_at=now))
    else:
        existing.last_heartbeat_at = now
        db.add(existing)
    await db.commit()


async def _heartbeat_while_running(
    session_factory: SessionFactory,
    *,
    job_id: uuid.UUID,
    lease_seconds: float,
    interval_seconds: float,
) -> None:
    """Runs until cancelled, renewing job_id's lease every
    interval_seconds - see module docstring."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with session_factory() as db:
                job = await db.get(Job, job_id)
                if job is None or job.status != JobStatus.RUNNING:
                    return
                await queue.renew_lease(db, job, lease_seconds=lease_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:  # broad on purpose - a failed heartbeat tick must never crash the worker
            logger.exception("heartbeat renewal failed for job %s", job_id)


async def process_one_job(
    session_factory: SessionFactory,
    gateway: LLMGateway,
    settings: Settings,
    *,
    worker_id: str,
    storage: StorageBackend,
) -> bool:
    """Claims and executes at most one job. Returns whether it did any
    work (False means the queue was empty right now)."""
    async with session_factory() as db:
        job = await queue.claim_next_job(
            db, worker_id=worker_id, lease_seconds=settings.job_lease_seconds
        )
        if job is None:
            return False

        logger.info("worker %s claimed job %s (%s)", worker_id, job.id, job.job_type)
        job_id = job.id
        heartbeat_task = asyncio.create_task(
            _heartbeat_while_running(
                session_factory,
                job_id=job_id,
                lease_seconds=settings.job_lease_seconds,
                interval_seconds=settings.job_heartbeat_interval_seconds,
            )
        )
        try:
            await process_claimed_job(db, gateway, settings, job=job, storage=storage)
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
    return True


async def run_worker(
    *,
    worker_id: str,
    session_factory: SessionFactory,
    gateway: LLMGateway,
    settings: Settings,
    shutdown_event: asyncio.Event,
    storage: StorageBackend,
) -> None:
    """The main loop. Runs a stale-job recovery pass on startup, then
    repeatedly claims+executes one job at a time (sleeping
    job_worker_poll_interval_seconds when the queue is empty), updating
    this worker's own heartbeat every iteration, until shutdown_event is
    set. A periodic recovery pass also runs on a slower cadence so a job
    whose worker crashed mid-execution doesn't wait forever for someone
    else to notice.

    `storage` is threaded through explicitly, same as `gateway` - only
    Sprint P10B3's asset_ingestion jobs need it (app/worker/dispatch.py's
    _process_asset_ingestion_job calls app.services.ingestion_service.
    ingest_asset(), which needs a StorageBackend the same way
    app/api/v1/assets.py does via FastAPI's Depends(get_storage_backend))
    - agent/workflow-run jobs ignore it entirely. Constructed once by
    app/worker/main.py's entrypoint via app.storage.factory.
    get_storage_backend(), the exact same "call the process-wide cached
    factory once, then pass the instance around explicitly" pattern
    already used for `gateway` (app.llm.factory.get_llm_gateway())."""
    started_at = datetime.now(timezone.utc)
    async with session_factory() as db:
        await upsert_heartbeat(db, worker_id, started_at=started_at)
        recovered = await queue.recover_stale_jobs(
            db, backoff_base_seconds=settings.job_retry_backoff_base_seconds
        )
        if recovered:
            logger.warning("worker %s: recovered %d stale job(s) on startup", worker_id, recovered)

    recovery_interval = timedelta(seconds=max(settings.job_lease_seconds, 30.0))
    last_recovery_check = datetime.now(timezone.utc)

    while not shutdown_event.is_set():
        did_work = await process_one_job(
            session_factory, gateway, settings, worker_id=worker_id, storage=storage
        )

        async with session_factory() as db:
            await upsert_heartbeat(db, worker_id, started_at=started_at)

        now = datetime.now(timezone.utc)
        if now - last_recovery_check >= recovery_interval:
            async with session_factory() as db:
                recovered = await queue.recover_stale_jobs(
                    db, backoff_base_seconds=settings.job_retry_backoff_base_seconds
                )
                if recovered:
                    logger.warning("worker %s: recovered %d stale job(s)", worker_id, recovered)
            last_recovery_check = now

        if not did_work:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=settings.job_worker_poll_interval_seconds
                )
