"""Health check endpoint.

Used by Docker healthchecks, load balancers, and CI to confirm the
service is up and its dependencies (currently: PostgreSQL, the job
queue, and - as of Sprint P8 - at least one worker process) are
reachable/alive.

Sprint P8 additions: `queue` and `worker`.
- `queue` reports the same connectivity `database` does, by design: the
  durable job queue (app.jobs) IS the `jobs` table in this same
  PostgreSQL database (see app/jobs/__init__.py's module docstring for
  the full queue-technology rationale) - there is no separate broker
  process to probe, so a working database connection already proves the
  queue is reachable.
- `worker` reports whether ANY worker process has sent a heartbeat
  (app.models.worker_heartbeat.WorkerHeartbeat, upserted by
  app/worker/runner.py) more recently than
  settings.worker_heartbeat_stale_after_seconds ago.

`status` deliberately stays derived from `database` alone, exactly as
before Sprint P8 - a missing worker means queued work will not be picked
up, but the API process itself (this endpoint's own subject) is still
fully up. Callers that specifically care about worker liveness should
read the `worker` field, not `status`; the Docker Compose worker
service's own HEALTHCHECK uses app/worker/healthcheck.py (a separate,
worker-side check) rather than polling this endpoint, precisely to keep
those two concerns independent.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.database.session import check_database_connection, get_db
from app.models.worker_heartbeat import WorkerHeartbeat

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    database: str
    queue: str
    worker: str


async def _check_worker_available(db: AsyncSession, settings: Settings) -> bool:
    """True if at least one worker heartbeat is fresher than
    worker_heartbeat_stale_after_seconds. A worker that crashed or was
    never started simply has no row, or a stale one - either way this
    returns False rather than raising, since "no worker" is an expected,
    non-error state (e.g. in tests, or before `docker compose up` has
    started the worker service)."""
    threshold = datetime.now(timezone.utc) - timedelta(
        seconds=settings.worker_heartbeat_stale_after_seconds
    )
    try:
        result = await db.execute(
            select(WorkerHeartbeat.worker_id).where(WorkerHeartbeat.last_heartbeat_at >= threshold)
        )
        return result.first() is not None
    except Exception:
        return False


@router.get("/health", response_model=HealthResponse)
async def health_check(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    db_ok = await check_database_connection()
    worker_ok = await _check_worker_available(db, settings) if db_ok else False
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database="connected" if db_ok else "unreachable",
        queue="connected" if db_ok else "unreachable",
        worker="available" if worker_ok else "unavailable",
    )
