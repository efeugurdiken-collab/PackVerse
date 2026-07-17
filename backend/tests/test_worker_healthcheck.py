"""Tests for app/worker/healthcheck.py (Sprint P8): the Docker
HEALTHCHECK script for the `worker` service - is_healthy() reports True
only when a fresh (non-stale) worker_heartbeats row exists for the
worker_id it looks up.

Runs against the isolated test database (via the `test_engine` fixture,
for schema creation) using its own plain psycopg2 connection, exactly as
the real script does - deliberately not going through db_session, since
is_healthy() is a synchronous, self-contained script by design (see its
module docstring) and must be tested as one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.config import get_settings as get_real_settings
from app.models.worker_heartbeat import WorkerHeartbeat
from app.worker import healthcheck


def _test_settings(**overrides: object) -> Settings:
    real = get_real_settings()
    db_name = real.test_postgres_db or f"{real.postgres_db}_test"
    defaults: dict[str, object] = {
        "postgres_user": real.postgres_user,
        "postgres_password": real.postgres_password,
        "postgres_host": real.postgres_host,
        "postgres_port": real.postgres_port,
        "postgres_db": db_name,
        "jwt_secret_key": "x" * 32,
    }
    defaults.update(overrides)
    return Settings(**defaults)


async def test_is_healthy_false_when_no_heartbeat_row(test_engine, monkeypatch) -> None:
    monkeypatch.setattr(healthcheck, "get_settings", lambda: _test_settings())
    monkeypatch.setattr(healthcheck, "default_worker_id", lambda: "no-such-worker")
    assert healthcheck.is_healthy() is False


async def test_is_healthy_true_for_fresh_heartbeat(test_engine, monkeypatch) -> None:
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        db.add(WorkerHeartbeat(worker_id="fresh-worker", started_at=now, last_heartbeat_at=now))
        await db.commit()

    settings = _test_settings(worker_heartbeat_stale_after_seconds=60.0)
    monkeypatch.setattr(healthcheck, "get_settings", lambda: settings)
    monkeypatch.setattr(healthcheck, "default_worker_id", lambda: "fresh-worker")

    assert healthcheck.is_healthy() is True


async def test_is_healthy_false_for_stale_heartbeat(test_engine, monkeypatch) -> None:
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    stale = datetime.now(timezone.utc) - timedelta(minutes=10)
    async with session_factory() as db:
        db.add(
            WorkerHeartbeat(worker_id="stale-worker", started_at=stale, last_heartbeat_at=stale)
        )
        await db.commit()

    settings = _test_settings(worker_heartbeat_stale_after_seconds=60.0)
    monkeypatch.setattr(healthcheck, "get_settings", lambda: settings)
    monkeypatch.setattr(healthcheck, "default_worker_id", lambda: "stale-worker")

    assert healthcheck.is_healthy() is False


async def test_is_healthy_false_when_database_unreachable(test_engine, monkeypatch) -> None:
    # A nonexistent database name fails fast (Postgres rejects the
    # connection immediately) - unlike an unreachable host/port, which
    # can hang for a full TCP timeout and make this test slow/flaky.
    unreachable = _test_settings(postgres_db="packverse_definitely_does_not_exist")
    monkeypatch.setattr(healthcheck, "get_settings", lambda: unreachable)
    monkeypatch.setattr(healthcheck, "default_worker_id", lambda: "irrelevant")
    assert healthcheck.is_healthy() is False
