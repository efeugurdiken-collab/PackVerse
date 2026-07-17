"""Worker process entrypoint (Sprint P8): `python -m app.worker`.

Wires app/worker/runner.py's `run_worker` to the real
app.database.session.AsyncSessionLocal, app.llm.factory.get_llm_gateway,
and app.core.config.get_settings - the same production dependencies
app/main.py's FastAPI app uses, obtained the exact same way, so the
worker and the API process share one engine/pool configuration and one
provider-registry cache mechanism (each is a separate OS process, so
each gets its own actual engine/registry instances - only the
construction path is shared).

Handles SIGTERM/SIGINT by setting the shutdown_event runner.run_worker
watches, so `docker compose stop` (SIGTERM, with a grace period before
SIGKILL) lets an in-flight job finish rather than being killed
mid-execution - the same reasoning as Job's lease/heartbeat mechanism,
applied to a clean shutdown instead of a crash.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from app.core.config import get_settings
from app.database.session import AsyncSessionLocal
from app.llm.factory import get_llm_gateway
from app.worker.runner import default_worker_id, run_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _amain() -> None:
    settings = get_settings()
    gateway = get_llm_gateway()
    worker_id = default_worker_id()
    shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            # add_signal_handler is unavailable on some platforms (e.g.
            # Windows event loops) - the worker is only ever deployed via
            # the Linux Docker image, but this keeps `python -m
            # app.worker` from crashing outright if run manually
            # elsewhere; graceful shutdown just degrades to SIGKILL.
            pass

    logger.info("worker %s starting (poll interval %.1fs, lease %.1fs)",
                worker_id, settings.job_worker_poll_interval_seconds, settings.job_lease_seconds)
    try:
        await run_worker(
            worker_id=worker_id,
            session_factory=AsyncSessionLocal,
            gateway=gateway,
            settings=settings,
            shutdown_event=shutdown_event,
        )
    finally:
        logger.info("worker %s shutting down", worker_id)


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
