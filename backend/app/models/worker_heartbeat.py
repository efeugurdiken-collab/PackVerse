"""WorkerHeartbeat model (Sprint P8): one row per live worker process,
upserted on every poll cycle.

Deliberately separate from Job's own heartbeat_at column - a Job only
has a heartbeat while it's actively RUNNING a specific job, so there is
no job row to check while a worker is idle (polling an empty queue).
This table answers "is any worker process alive at all right now",
independent of whether it currently has work - see the sprint's
"worker heartbeat/availability reporting" requirement and
app/api/v1/health.py's use of it.

worker_id defaults to the process hostname (see app/worker/runner.py) -
inside Docker this is the container id, which is also what a
same-container HEALTHCHECK script can look itself up by, without needing
to know its own id through any other channel.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class WorkerHeartbeat(Base, TimestampMixin):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<WorkerHeartbeat worker_id={self.worker_id!r} "
            f"last_heartbeat_at={self.last_heartbeat_at}>"
        )
