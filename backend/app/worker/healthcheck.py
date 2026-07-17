"""Docker HEALTHCHECK script for the `worker` service (Sprint P8):
`python -m app.worker.healthcheck`, exit 0 = healthy, exit 1 = unhealthy.

Deliberately a small synchronous script, not an import of anything from
app/worker/runner.py or app/worker/main.py - it runs as a *separate*
short-lived process launched by the Docker daemon on every HEALTHCHECK
interval, alongside (not inside) the actual long-running worker process,
so it must not share event loops, sessions, or in-memory state with it.
It reuses app.core.config.get_settings and app.worker.runner.
default_worker_id purely for their pure, side-effect-free return values
(the same worker-id derivation the real worker process uses - WORKER_ID
env var if set, else hostname - so this check looks up the exact same
worker_heartbeats row the running worker itself is updating), and opens
its own plain synchronous psycopg2 connection via
settings.sync_database_url (the same driver/URL Alembic already uses)
rather than pulling in the async engine machinery app/database/session.py
owns, which is unnecessary for a single one-shot query.

"Healthy" means: a worker_heartbeats row exists for this worker_id and
its last_heartbeat_at is more recent than
settings.worker_heartbeat_stale_after_seconds ago. This mirrors exactly
what app/api/v1/health.py's /health endpoint reports for worker
availability (see that module's docstring) - the Docker-level check and
the HTTP-level check intentionally use the same staleness threshold so
they never disagree about whether the worker is alive.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import psycopg2

from app.core.config import get_settings
from app.worker.runner import default_worker_id


def is_healthy() -> bool:
    settings = get_settings()
    worker_id = default_worker_id()
    try:
        conn = psycopg2.connect(settings.sync_database_url.replace("+psycopg2", ""))
    except Exception:
        return False

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_heartbeat_at FROM worker_heartbeats WHERE worker_id = %s",
                (worker_id,),
            )
            row = cur.fetchone()
    except Exception:
        return False
    finally:
        conn.close()

    if row is None or row[0] is None:
        return False

    last_heartbeat_at: datetime = row[0]
    if last_heartbeat_at.tzinfo is None:
        last_heartbeat_at = last_heartbeat_at.replace(tzinfo=timezone.utc)

    age_seconds = (datetime.now(timezone.utc) - last_heartbeat_at).total_seconds()
    return age_seconds <= settings.worker_heartbeat_stale_after_seconds


def main() -> None:
    sys.exit(0 if is_healthy() else 1)


if __name__ == "__main__":
    main()
