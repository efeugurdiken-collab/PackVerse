"""Durable Job Queue (Sprint P8: Asynchronous Job Execution).

Moves P6 agent-run and P7 workflow-run execution off the synchronous
HTTP request path and onto a durable, database-backed queue: the API
now only validates + persists a QUEUED run and a QUEUED Job in one
transaction (see enqueue_agent_run/enqueue_workflow_run in service.py)
and returns 202 Accepted; a separate worker process (app/worker/) claims
and executes jobs, reusing the existing P6/P7 executors unchanged.

Chosen queue technology: PostgreSQL itself, via a `jobs` table and
`SELECT ... FOR UPDATE SKIP LOCKED` (see queue.py's claim_next_job) -
not Redis/RabbitMQ/Celery/arq or any other broker. Rationale (see the
Sprint P8 report's "selected queue technology and rationale" for the
full writeup):

- The sprint spec explicitly names "a durable database-backed job table
  with worker polling" as an acceptable enqueue-safety pattern, and
  explicitly says "do not build a custom message broker" - SKIP LOCKED
  is a well-established Postgres idiom for exactly this, not a
  hand-rolled broker.
- This project already runs PostgreSQL as its only stateful service;
  adding Redis/RabbitMQ would be new infrastructure, a new Docker
  Compose service, a new client library dependency, and a second
  failure domain to operate - for the "smallest production-sensible
  queue technology" the spec asks for, reusing what's already there
  wins over introducing a new one.
- True same-transaction atomicity for enqueue-safety (run row + job row,
  one commit, no dual-write) is only straightforward when the queue and
  the business data live in the same database - which a DB-backed queue
  gives for free and an external broker cannot.
- asyncpg (already this project's only async Postgres driver) supports
  `FOR UPDATE SKIP LOCKED` natively, and the existing transactional test
  fixtures (tests/conftest.py's db_session) work unchanged for testing
  the queue in isolation.

No FastAPI or transport-layer imports in this package. Never imports
app.llm/app.services.llm_service directly and never duplicates P6/P7's
own execution logic - see app/worker/dispatch.py, which is the only
place a job's payload gets handed to app.runtime.executor.execute_run or
app.workflows.executor.execute_workflow_run.
"""
