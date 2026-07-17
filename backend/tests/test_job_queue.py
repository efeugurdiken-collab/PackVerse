"""Tests for the low-level durable queue primitives (Sprint P8):
app/jobs/queue.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.jobs import queue
from app.jobs.exceptions import InvalidJobTransitionError
from app.models.enums import JobStatus


# --- claim_next_job ---------------------------------------------------


async def test_claim_next_job_returns_none_when_queue_empty(db_session) -> None:
    claimed = await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=60.0)
    assert claimed is None


async def test_claim_next_job_claims_oldest_queued_job(db_session, make_job) -> None:
    # created_at is explicit here (rather than relying on two real-time
    # commits landing in distinguishable milliseconds) so the "oldest
    # first" ordering assertion below is deterministic, not merely likely.
    now = datetime.now(timezone.utc)
    older = await make_job(created_at=now - timedelta(minutes=5))
    await make_job(created_at=now)  # newer

    claimed = await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=60.0)

    assert claimed is not None
    assert claimed.id == older.id
    assert claimed.status == JobStatus.RUNNING
    assert claimed.worker_id == "w1"
    assert claimed.attempt_count == 1
    assert claimed.lease_expires_at is not None
    assert claimed.started_at is not None


async def test_claim_next_job_skips_jobs_not_yet_due(db_session, make_job) -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    await make_job(status=JobStatus.RETRYING, next_attempt_at=future)

    claimed = await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=60.0)

    assert claimed is None


async def test_claim_next_job_claims_due_retrying_job(db_session, make_job) -> None:
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    job = await make_job(status=JobStatus.RETRYING, next_attempt_at=past, attempt_count=1)

    claimed = await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=60.0)

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.RUNNING
    assert claimed.attempt_count == 2  # incremented on claim


async def test_claim_next_job_never_claims_running_or_terminal_jobs(db_session, make_job) -> None:
    await make_job(status=JobStatus.RUNNING)
    await make_job(status=JobStatus.COMPLETED)
    await make_job(status=JobStatus.FAILED)
    await make_job(status=JobStatus.CANCELLED)

    claimed = await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=60.0)

    assert claimed is None


async def test_claim_next_job_preserves_started_at_across_retries(db_session, make_job) -> None:
    """A job re-claimed after a retry keeps its original started_at (the
    time work FIRST began), not the time of this particular attempt."""
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    original_started = datetime.now(timezone.utc) - timedelta(hours=2)
    job = await make_job(
        status=JobStatus.RETRYING,
        next_attempt_at=past,
        attempt_count=1,
        started_at=original_started,
    )

    claimed = await queue.claim_next_job(db_session, worker_id="w1", lease_seconds=60.0)

    assert claimed is not None
    assert claimed.id == job.id
    assert abs((claimed.started_at - original_started).total_seconds()) < 1


# --- renew_lease --------------------------------------------------------


async def test_renew_lease_extends_expiry_and_heartbeat(db_session, make_job) -> None:
    job = await make_job(status=JobStatus.RUNNING)
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.add(job)
    await db_session.commit()

    await queue.renew_lease(db_session, job, lease_seconds=120.0)

    assert job.lease_expires_at > datetime.now(timezone.utc)
    assert job.heartbeat_at is not None


# --- mark_completed / mark_failed / mark_retrying -----------------------


async def test_mark_completed_from_running(db_session, make_job) -> None:
    job = await make_job(status=JobStatus.RUNNING)
    await queue.mark_completed(db_session, job)
    assert job.status == JobStatus.COMPLETED
    assert job.completed_at is not None
    assert job.lease_expires_at is None


async def test_mark_completed_from_queued_raises(db_session, make_job) -> None:
    job = await make_job(status=JobStatus.QUEUED)
    with pytest.raises(InvalidJobTransitionError):
        await queue.mark_completed(db_session, job)


async def test_mark_failed_sets_error_fields(db_session, make_job) -> None:
    job = await make_job(status=JobStatus.RUNNING)
    await queue.mark_failed(db_session, job, error_code="Boom", error_message="it broke")
    assert job.status == JobStatus.FAILED
    assert job.error_code == "Boom"
    assert job.error_message == "it broke"
    assert job.completed_at is not None


async def test_mark_retrying_sets_backoff_and_clears_worker(db_session, make_job) -> None:
    job = await make_job(status=JobStatus.RUNNING, attempt_count=1)
    job.worker_id = "w1"
    db_session.add(job)
    await db_session.commit()

    before = datetime.now(timezone.utc)
    await queue.mark_retrying(
        db_session, job, error_code="Transient", error_message="try again", backoff_seconds=30.0
    )

    assert job.status == JobStatus.RETRYING
    assert job.worker_id is None
    assert job.lease_expires_at is None
    assert job.next_attempt_at is not None
    assert job.next_attempt_at > before + timedelta(seconds=29)


# --- mark_failed_or_retry -------------------------------------------------


async def test_mark_failed_or_retry_retries_when_attempts_remain(db_session, make_job) -> None:
    job = await make_job(status=JobStatus.RUNNING, attempt_count=1, max_attempts=3)
    result = await queue.mark_failed_or_retry(
        db_session, job, error_code="Boom", error_message="oops", backoff_base_seconds=5.0
    )
    assert result == JobStatus.RETRYING
    assert job.status == JobStatus.RETRYING


async def test_mark_failed_or_retry_fails_when_attempts_exhausted(db_session, make_job) -> None:
    job = await make_job(status=JobStatus.RUNNING, attempt_count=3, max_attempts=3)
    result = await queue.mark_failed_or_retry(
        db_session, job, error_code="Boom", error_message="oops", backoff_base_seconds=5.0
    )
    assert result == JobStatus.FAILED
    assert job.status == JobStatus.FAILED


def test_compute_backoff_seconds_is_exponential() -> None:
    assert queue.compute_backoff_seconds(1, base_seconds=5.0) == 5.0
    assert queue.compute_backoff_seconds(2, base_seconds=5.0) == 10.0
    assert queue.compute_backoff_seconds(3, base_seconds=5.0) == 20.0


def test_compute_backoff_seconds_never_negative_exponent() -> None:
    # attempt_count 0 (defensive) should not raise or go negative.
    assert queue.compute_backoff_seconds(0, base_seconds=5.0) == 5.0


# --- mark_cancelled / cancel_queued_job ----------------------------------


async def test_mark_cancelled_from_running(db_session, make_job) -> None:
    job = await make_job(status=JobStatus.RUNNING)
    await queue.mark_cancelled(db_session, job)
    assert job.status == JobStatus.CANCELLED
    assert job.completed_at is not None
    assert job.cancel_requested_at is not None


async def test_cancel_queued_job_from_queued(db_session, make_job) -> None:
    job = await make_job(status=JobStatus.QUEUED)
    await queue.cancel_queued_job(db_session, job)
    assert job.status == JobStatus.CANCELLED


async def test_cancel_queued_job_from_running_raises(db_session, make_job) -> None:
    job = await make_job(status=JobStatus.RUNNING)
    with pytest.raises(InvalidJobTransitionError):
        await queue.cancel_queued_job(db_session, job)


# --- recover_stale_jobs ---------------------------------------------------


async def test_recover_stale_jobs_ignores_jobs_with_valid_lease(db_session, make_job) -> None:
    """A RUNNING job whose lease has NOT expired must never be touched -
    this is the core safety property recover_stale_jobs exists to
    preserve (a live worker's in-progress job must never be reclaimed out
    from under it)."""
    job = await make_job(status=JobStatus.RUNNING)
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db_session.add(job)
    await db_session.commit()

    recovered = await queue.recover_stale_jobs(db_session, backoff_base_seconds=5.0)

    assert recovered == 0
    await db_session.refresh(job)
    assert job.status == JobStatus.RUNNING


async def test_recover_stale_jobs_reclaims_expired_lease_with_attempts_remaining(
    db_session, make_job
) -> None:
    job = await make_job(status=JobStatus.RUNNING, attempt_count=1, max_attempts=3)
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    job.worker_id = "dead-worker"
    db_session.add(job)
    await db_session.commit()

    recovered = await queue.recover_stale_jobs(db_session, backoff_base_seconds=5.0)

    assert recovered == 1
    await db_session.refresh(job)
    assert job.status == JobStatus.RETRYING
    assert job.worker_id is None
    assert job.error_code == "WorkerLeaseExpired"
    assert job.next_attempt_at is not None


async def test_recover_stale_jobs_fails_when_attempts_exhausted(db_session, make_job) -> None:
    job = await make_job(status=JobStatus.RUNNING, attempt_count=3, max_attempts=3)
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.add(job)
    await db_session.commit()

    recovered = await queue.recover_stale_jobs(db_session, backoff_base_seconds=5.0)

    assert recovered == 1
    await db_session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "WorkerLeaseExpired"


async def test_recover_stale_jobs_never_touches_completed_jobs(db_session, make_job) -> None:
    """Completed work must never be replayed - recover_stale_jobs only
    ever looks at RUNNING jobs, regardless of how old a COMPLETED job's
    (unused, null-by-then) lease_expires_at might be."""
    job = await make_job(status=JobStatus.COMPLETED)
    db_session.add(job)
    await db_session.commit()

    recovered = await queue.recover_stale_jobs(db_session, backoff_base_seconds=5.0)

    assert recovered == 0
    await db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


async def test_recover_stale_jobs_is_idempotent(db_session, make_job) -> None:
    job = await make_job(status=JobStatus.RUNNING, attempt_count=1, max_attempts=3)
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.add(job)
    await db_session.commit()

    first = await queue.recover_stale_jobs(db_session, backoff_base_seconds=5.0)
    second = await queue.recover_stale_jobs(db_session, backoff_base_seconds=5.0)

    assert first == 1
    assert second == 0  # already RETRYING now, not RUNNING - nothing left to reclaim
