"""Background worker process (Sprint P8: Asynchronous Job Execution).

Runs as a separate `docker compose` service (see docker-compose.yml's
`worker` service and this Dockerfile's shared image), started with
`python -m app.worker`. Claims jobs from the app.jobs durable queue and
executes them by calling straight into the existing, unmodified P6/P7
executors (app.runtime.executor.execute_run /
app.workflows.executor.execute_workflow_run) - see dispatch.py's module
docstring for the full "never duplicate execution logic" reasoning.

No FastAPI imports anywhere in this package - it is a plain asyncio
process, not a web server. app/api/v1/health.py reads this process's
liveness signal (app.models.worker_heartbeat.WorkerHeartbeat) but does
not import anything from here.
"""
