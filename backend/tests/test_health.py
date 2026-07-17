"""Tests for the /health endpoint.

Note: these run against whatever PostgreSQL instance is configured via
environment variables. In CI/local dev, run via docker compose so the
db service is available - see README.md.

pytest-asyncio is configured with asyncio_mode = "auto" in pyproject.toml,
so async test functions are picked up automatically without markers.
"""


async def test_root_endpoint(client) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"


async def test_health_endpoint_returns_200(client) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["database"] in {"connected", "unreachable"}
    # Sprint P8: queue connectivity (the same PostgreSQL connection - see
    # app/api/v1/health.py's module docstring for why there is no
    # separate broker to probe) and worker availability (based on
    # worker_heartbeats freshness).
    assert body["queue"] in {"connected", "unreachable"}
    assert body["worker"] in {"available", "unavailable"}


async def test_health_endpoint_reports_worker_available_with_a_fresh_heartbeat(
    client, db_session
) -> None:
    from datetime import datetime, timezone

    from app.models.worker_heartbeat import WorkerHeartbeat

    now = datetime.now(timezone.utc)
    db_session.add(WorkerHeartbeat(worker_id="test-worker", started_at=now, last_heartbeat_at=now))
    await db_session.commit()

    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["worker"] == "available"


async def test_health_endpoint_reports_worker_unavailable_with_no_heartbeat(client) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["worker"] == "unavailable"


async def test_health_endpoint_reports_worker_unavailable_with_a_stale_heartbeat(
    client, db_session
) -> None:
    from datetime import datetime, timedelta, timezone

    from app.models.worker_heartbeat import WorkerHeartbeat

    stale = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.add(
        WorkerHeartbeat(worker_id="stale-worker", started_at=stale, last_heartbeat_at=stale)
    )
    await db_session.commit()

    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["worker"] == "unavailable"
