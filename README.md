# PackVerse Platform

The runtime implementation of PackVerse OS, whose specification lives in a
separate, frozen Obsidian vault (`00 Company` through `10 Roadmap`). This
repository is the codebase; the vault is the spec. Do not merge them.

**Sprint P1 scope:** infrastructure foundation only. No AI features, no
Agents, no MCP, no RAG yet - those arrive in later sprints per the vault's
`10 Roadmap/Current Sprint.md` implementation order.

**Sprint P2 scope:** the first real database and domain foundation -
`Product`, `Asset`, `Job`, `AgentDefinition`, `WorkflowDefinition` models,
a hand-written Alembic migration, Pydantic v2 schemas, and a minimal
Product CRUD API (create / read / list / update, no delete).

**Sprint P3 scope:** authentication and role-based access control - a
`User` model (viewer/operator/admin roles), Argon2id password hashing,
JWT access/refresh tokens, `/api/v1/auth/*` endpoints, and authorization
on the Product API (viewer: read-only, operator/admin: create + update).
Still no storage, LLM integration, Agents runtime, MCP, or RAG - and no
OAuth providers, email verification delivery, or password reset yet
(explicitly out of scope for this sprint). A follow-up change also made
`JWT_SECRET_KEY` optional specifically in `ENVIRONMENT=development`: if
it's missing there, the app generates one and appends it to `.env`
itself rather than refusing to start - see Setup below. It remains
mandatory (no auto-generation) for staging/production/test.

**Sprint P4 scope:** a storage abstraction (`app/storage/`) with a local
filesystem backend and an S3-compatible backend (AWS S3, MinIO,
Cloudflare R2, ...) selected via `STORAGE_BACKEND`, plus the Asset API
(`/api/v1/products/{id}/assets` upload/list, `/api/v1/assets/{id}`
detail/download/delete). Uploads are validated (size, MIME allowlist,
best-effort magic-byte check), written to storage before the database
row is committed, and rolled back from storage if that commit fails.
Deletes are soft (status + `deleted_at`) and idempotent. Still no LLM
integration, Agents runtime, MCP, or RAG.

**Sprint P5 scope:** a provider-agnostic LLM Gateway (`app/llm/`) - a
typed `LLMProvider` interface with Anthropic, OpenAI-compatible
(OpenAI/OpenRouter/any local OpenAI-compatible server), and a
network-free `fake` provider for tests and credential-free smoke
testing; provider/model-alias routing, a retry policy for transient
failures, gateway-level JSON Schema structured-output validation,
decimal-safe configurable cost estimation, and the
`/api/v1/llm/{generate,providers,models,health,requests/{id}}`
endpoints. A new `llm_requests` table persists routing/usage/cost/
latency metadata for every call - never the prompt or generated content.

**Sprint P6 scope:** an AI Runtime (`app/runtime/`) that executes an
`AgentDefinition` through the Sprint P5 LLM Gateway - a validated
`AgentRun` lifecycle (`queued -> running -> completed/failed/cancelled`),
a reusable prompt builder, an executor that calls
`app.services.llm_service.generate_and_persist` (so every run also shows
up in the P5 `llm_requests` audit trail), and the
`/api/v1/runs/{,{id},{id}/cancel}` endpoints. A new `agent_runs` table
persists status/timestamps/duration/provider/model/tokens/cost/output/
error for every run. Still no autonomous multi-step agent loops, MCP,
RAG, tool execution, or background job queue.

**Sprint P7 scope:** Workflow Orchestration (`app/workflows/`) - executes
a `WorkflowDefinition`'s ordered steps sequentially, each step through
the Sprint P6 AI Runtime (never calling `app.llm`/
`app.services.llm_service` directly). A validated `WorkflowRun` lifecycle
(`queued -> running -> completed/failed/cancelled`) and a `WorkflowStepRun`
lifecycle per step (`pending -> running -> completed/failed/cancelled/skipped`),
a closed/deterministic `input_mapping` vocabulary
(`workflow_input`/`previous_output`/`step_output`/`static` - never a
general-purpose template language), a reusable input-builder component,
and the `/api/v1/workflow-runs/{,{id},{id}/steps,{id}/cancel}` endpoints.
New `workflow_runs` and `workflow_step_runs` tables persist status/
timestamps/duration/output/error for every run and step - `workflow_runs`
follows P6's "no raw input" posture, but `workflow_step_runs` DOES
persist `input_snapshot` per the sprint's explicit requirement. On a step
failure, that step becomes `failed`, every later still-`pending` step
becomes `skipped`, and the run becomes `failed` - no step after the
failed one runs. Still no parallel execution, DAG branching, human
approval steps, whole-workflow retries, cron/scheduled workflows,
distributed queues, MCP, RAG, or streaming.

**Sprint P8 scope:** Asynchronous Job Execution - `POST /api/v1/runs` and
`POST /api/v1/workflow-runs` now validate and *enqueue* a run instead of
executing it synchronously, returning `202 Accepted` immediately with the
run in its `queued` state. A separate `worker` process (`app/worker/`,
started via `docker compose`'s new `worker` service or `python -m
app.worker`) polls a durable, PostgreSQL-table-backed job queue
(`app/jobs/` - the pre-existing but previously-unused `jobs` table,
extended with attempt/lease/heartbeat/worker columns; see "Job Queue &
Worker" below for the full queue-technology rationale) and executes jobs
by calling the exact same, unmodified P6 (`execute_run`) and P7
(`execute_workflow_run`) executors this sprint's API endpoints used to
call directly. A `Job` lifecycle (`queued -> running ->
completed/failed/retrying/cancelled`) tracks attempts, leases,
heartbeats, and errors; failures are retried with exponential backoff
only when they're genuinely transient infrastructure problems (never for
invalid definitions, inactive agents, malformed input, or a run that's
already terminal); stale jobs from a crashed worker are recovered via
lease-timeout on startup and periodically; cancellation is immediate for
queued work and cooperative (checked between workflow steps) for
in-flight work; and `/api/v1/health` now reports queue connectivity and
worker availability alongside database connectivity. Still no scheduled/
cron jobs, parallel workflow steps, DAG branching, WebSocket/streaming
progress, human-approval steps, or a Kubernetes-style orchestrator.

> P1-P6 were all written in a sandboxed environment with no Docker and
> no external network access, then verified locally by the maintainer.
> P1/P2: pytest (20 passed), ruff, mypy all passed. P3: 58 passed, mypy
> clean across 39 source files. P4: 129 passed (2 pre-existing Starlette
> deprecation warnings, not failures), ruff clean, mypy clean across 47
> source files - after two follow-up fixes caught by real local runs (a
> missing `pathlib` import, and a `MissingGreenlet`/SQLAlchemy
> identity-map bug in one rollback test). P5: 218 passed (same 2
> pre-existing warnings), ruff clean, mypy clean across 64 source files -
> after two follow-up fixes caught by real local runs (an invalid `noqa`
> directive, and two stale/inverted assertions in migration tests left
> over from adding the P5 `llm_requests` table). P6, P7, and P8 were all
> written and statically validated (`python -m py_compile` plus a manual
> unused-import/line-length sweep, not executed) the same way, in an
> environment with no Docker and no network access - P7 immediately
> after P6, and P8 immediately after P7, per explicit CTO instruction to
> proceed without waiting for the prior sprint's local verification. All
> three sprints' real local `./verify.sh` results are outstanding
> simultaneously as of P8. See
> [`docs/P1_LOCAL_VERIFICATION.md`](docs/P1_LOCAL_VERIFICATION.md) for
> the full history and exact reproduction steps.

## Tech Stack

- Python 3.13+
- FastAPI
- PostgreSQL 16
- SQLAlchemy 2.x (async, via `asyncpg`)
- Alembic (migrations, via sync `psycopg2` driver)
- Docker / Docker Compose
- Pydantic Settings
- Uvicorn
- Pytest

## Project Structure

```
packverse-platform/
├── backend/
│   ├── app/
│   │   ├── api/           # FastAPI routers (versioned under v1/) + deps.py (authorization)
│   │   ├── core/           # config, logging, security.py (password hashing + JWT)
│   │   ├── database/        # SQLAlchemy engine/session
│   │   ├── models/          # ORM models: Product, Asset, Job, AgentDefinition, WorkflowDefinition, User
│   │   ├── schemas/          # Pydantic v2 request/response schemas
│   │   ├── services/        # business logic (product_service.py, user_service.py, asset_service.py)
│   │   ├── storage/          # storage abstraction: base.py (interface), local.py, s3.py, factory.py
│   │   ├── llm/               # LLM Gateway: base.py (interface), providers/ (anthropic, openai_compatible, fake),
│   │   │                      #   gateway.py, routing.py, pricing.py, factory.py, models.py, exceptions.py
│   │   ├── runtime/          # AI Runtime: exceptions.py, models.py (state machine), prompt_builder.py,
│   │   │                      #   service.py (create/get/list/cancel), executor.py (execute_run)
│   │   ├── agents/          # Autonomous multi-step agent loops (empty - out of scope through P7)
│   │   ├── workflows/       # Workflow Orchestration: exceptions.py, models.py (state machines),
│   │   │                      #   definition.py (steps parsing/validation), input_builder.py,
│   │   │                      #   service.py (create/get/list/cancel/steps), executor.py (execute_workflow_run)
│   │   ├── jobs/             # Durable job queue (Sprint P8): exceptions.py, models.py (state machine),
│   │   │                      #   queue.py (claim/heartbeat/complete/fail/retry/cancel/recover - low level),
│   │   │                      #   service.py (enqueue_agent_run/enqueue_workflow_run/cancel_* - API-facing)
│   │   ├── worker/           # Standalone worker process (Sprint P8): dispatch.py (Job -> P6/P7 executor),
│   │   │                      #   runner.py (poll loop + lease renewal + heartbeat), main.py/__main__.py
│   │   │                      #   (`python -m app.worker` entrypoint), healthcheck.py (Docker HEALTHCHECK)
│   │   └── main.py         # FastAPI app entrypoint
│   ├── tests/                # model, API, auth, authorization, storage, asset, LLM gateway, AI runtime,
│   │                          #   workflow orchestration, job queue, worker, migration, and health tests
│   ├── alembic/
│   │   └── versions/
│   │       ├── 06b17a0f30ad_create_domain_tables.py   # P2 schema baseline
│   │       ├── 1f20f57819a3_create_users_table.py     # P3: users table
│   │       ├── ae14cc314d2f_extend_assets_for_storage.py  # P4: storage columns on assets
│   │       ├── 7c19e4b8a2d6_create_llm_requests_table.py  # P5: llm_requests table
│   │       ├── a1c8f7d2b3e9_create_agent_runs_table.py    # P6: agent_runs table
│   │       ├── d4e6b9a3f1c7_create_workflow_run_tables.py # P7: workflow_runs, workflow_step_runs tables
│   │       └── b7f3e9a1c5d2_add_job_queue_fields_and_worker_heartbeats.py # P8: jobs queue columns + worker_heartbeats
│   ├── Dockerfile
│   └── pyproject.toml
├── docker-compose.yml       # db, backend, worker (Sprint P8) services
├── .env.example
├── .gitignore
├── docs/
│   └── P1_LOCAL_VERIFICATION.md
└── README.md
```

## Setup

### 1. Configure environment

```bash
cp .env.example .env
# edit .env and set a real POSTGRES_PASSWORD
```

JWT_SECRET_KEY can be left blank for local development - see the comment
above it in `.env.example`. The app generates one on first boot and
writes it back into `.env` for you (only when `ENVIRONMENT=development`;
staging/production/test still require it set explicitly beforehand):

```bash
# optional - only if you want to set it yourself instead of auto-generating
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
# paste the output as JWT_SECRET_KEY= in .env
```

`STORAGE_BACKEND` defaults to `local`, which writes uploaded files under
`STORAGE_LOCAL_ROOT` (default `./data/storage`, gitignored - never
commit uploaded files). To use S3 or an S3-compatible provider (MinIO,
Cloudflare R2, ...) instead, set `STORAGE_BACKEND=s3` and fill in every
`S3_*` setting in `.env.example` - startup fails fast if any are missing.

The LLM Gateway needs no configuration to boot - `LLM_ALLOWED_PROVIDERS`
defaults to `anthropic,openai,fake`, and every provider's credentials are
only checked lazily, when that specific provider is actually selected
for a call (see `app/llm/factory.py`). To exercise `/api/v1/llm/generate`
without any real API key, set `LLM_DEFAULT_PROVIDER=fake` (or pass
`"provider": "fake"` per-request) - the `fake` provider never makes a
network call. To use a real provider, set `ANTHROPIC_API_KEY` and/or
`OPENAI_API_KEY` (plus `OPENAI_BASE_URL` if pointing at an
OpenAI-compatible server other than OpenAI itself) in `.env.example`.

The AI Runtime (`POST /api/v1/runs`) needs no configuration of its own -
it reuses the LLM Gateway settings above (an `AgentDefinition`'s
`configuration_json` picks the model/provider per-agent; see the Agent
Run API section below). There is no `AgentDefinition` CRUD API yet -
definitions are inserted directly (seeded from the vault's
`05 Agents/` specifications, per Sprint P2's design), so exercising
`POST /runs` requires an `agent_definitions` row to already exist.

### 2. Start the stack

```bash
docker compose up --build
```

This starts PostgreSQL, the FastAPI backend, and (Sprint P8) the
background `worker` process - all three wait for the database's
healthcheck to pass before starting; the worker uses the same image as
`backend`, just a different command (`python -m app.worker`) and its own
Docker `HEALTHCHECK` (`app/worker/healthcheck.py`, checking its own
`worker_heartbeats` freshness). Run migrations (see Database Migrations
below) before either `backend` or `worker` can do anything useful - the
worker will simply find an empty queue until then.

### 3. Verify

```bash
curl http://localhost:8000/
curl http://localhost:8000/api/v1/health
docker compose logs worker
```

`/api/v1/health` returns
`{"status": "ok", "database": "connected", "queue": "connected",
"worker": "available"}` once all three services are healthy and the
worker has sent at least one heartbeat (`queue` mirrors `database` by
design - the job queue lives in the same PostgreSQL instance, see "Job
Queue & Worker" below; `worker` only flips to `available` once
`worker_heartbeats` has a fresh row, which can take a few seconds after
first boot).

Interactive API docs: http://localhost:8000/docs

## Database Migrations

Migrations are managed with Alembic and run inside the backend container
(or locally if you have the dependencies installed):

```bash
# generate a new migration after adding/changing a model in app/models/
docker compose exec backend alembic revision --autogenerate -m "describe change"

# apply migrations
docker compose exec backend alembic upgrade head
```

Alembic reads its database URL from `app.core.config.Settings`, not from a
separately hardcoded string, so it always targets the same database the
app itself connects to. This applies to the running app; the test suite
overrides it to point at `settings.test_sync_database_url` instead (see
Tests below).

All seven migrations (`06b17a0f30ad_create_domain_tables.py` for P2,
`1f20f57819a3_create_users_table.py` for P3,
`ae14cc314d2f_extend_assets_for_storage.py` for P4,
`7c19e4b8a2d6_create_llm_requests_table.py` for P5,
`a1c8f7d2b3e9_create_agent_runs_table.py` for P6,
`d4e6b9a3f1c7_create_workflow_run_tables.py` for P7,
`b7f3e9a1c5d2_add_job_queue_fields_and_worker_heartbeats.py` for P8) were
written by hand rather than autogenerated - the sandbox this repo was
built in has no PostgreSQL instance to diff against. P8's migration only
adds columns to the pre-existing (P2-era, previously unused) `jobs`
table and creates the new `worker_heartbeats` table - it does not touch
or drop any prior table. Verify the full chain locally:

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current          # -> b7f3e9a1c5d2 (head)
docker compose exec backend alembic downgrade -1      # drops worker_heartbeats + P8's jobs columns only
docker compose exec backend alembic downgrade d4e6b9a3f1c7   # back to P7 head
docker compose exec backend alembic downgrade a1c8f7d2b3e9   # back to P6 head
docker compose exec backend alembic downgrade 7c19e4b8a2d6   # back to P5 head
docker compose exec backend alembic downgrade ae14cc314d2f   # back to P4 head
docker compose exec backend alembic downgrade 06b17a0f30ad   # drops users, keeps P2 tables
docker compose exec backend alembic downgrade base
docker compose exec backend alembic upgrade head
```

## Auth API (Sprint P3)

| Method | Path                    | Auth required | Description |
|--------|-------------------------|----------------|-------------|
| POST   | `/api/v1/auth/register` | No             | Create an account. Always `role: viewer`, `status: active`, `is_verified: false` - a client cannot set these. 409 on duplicate email. |
| POST   | `/api/v1/auth/login`    | No             | Exchange email + password for an access/refresh token pair. Generic 401 on any failure (wrong password, unknown email, or disabled account) so failed logins can't be used to enumerate accounts. |
| POST   | `/api/v1/auth/refresh`  | Refresh token  | Exchange a refresh token for a new (rotated) access/refresh pair. 401 if given an access token instead. |
| GET    | `/api/v1/auth/me`       | Access token   | Return the authenticated user. |

Access tokens expire in `ACCESS_TOKEN_EXPIRE_MINUTES` (default 15);
refresh tokens in `REFRESH_TOKEN_EXPIRE_DAYS` (default 30). Send the
access token as `Authorization: Bearer <token>`.

## Product API (Sprint P2, authorization added in P3)

Every endpoint below requires a valid access token (401 if missing,
malformed, expired, or the wrong token type). Read endpoints accept any
active role; create/update require `operator` or `admin` - `viewer` gets
403.

| Method | Path                     | Role required | Description                          |
|--------|--------------------------|----------------|---------------------------------------|
| POST   | `/api/v1/products`       | operator, admin | Create a product. 409 on duplicate `slug`. |
| GET    | `/api/v1/products/{id}`  | any active role | Fetch one product. 404 if unknown.    |
| GET    | `/api/v1/products`       | any active role | Paginated list (`limit`, `offset` query params). |
| PATCH  | `/api/v1/products/{id}`  | operator, admin | Partial update. `slug`, `product_type`, and `version` are immutable. 404 if unknown. |

There is intentionally no DELETE endpoint in this sprint.

## Asset API (Sprint P4)

Every endpoint requires a valid access token. Upload and delete require
`operator` or `admin`; list/detail/download accept any active role.

| Method | Path                                    | Role required    | Description |
|--------|------------------------------------------|-------------------|-------------|
| POST   | `/api/v1/products/{product_id}/assets`  | operator, admin   | Upload a file (`multipart/form-data`: `file`, optional `asset_type`). 404 unknown product, 422 empty/invalid filename, 413 too large, 415 unsupported type. |
| GET    | `/api/v1/products/{product_id}/assets`  | any active role   | Paginated list of that product's non-deleted assets. |
| GET    | `/api/v1/assets/{asset_id}`             | any active role   | Fetch one asset's metadata. 404 if unknown or deleted. |
| GET    | `/api/v1/assets/{asset_id}/download`    | any active role   | Local backend: streams the file. S3 backend: `307` redirect to a short-lived signed URL. |
| DELETE | `/api/v1/assets/{asset_id}`             | operator, admin   | Soft delete (idempotent, `204` even if already deleted). |

Example (local backend, using an operator's access token):

```bash
curl -X POST http://localhost:8000/api/v1/products/$PRODUCT_ID/assets \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@./logo.png" -F "asset_type=preview"

curl http://localhost:8000/api/v1/products/$PRODUCT_ID/assets \
  -H "Authorization: Bearer $TOKEN"

curl -OJ http://localhost:8000/api/v1/assets/$ASSET_ID/download \
  -H "Authorization: Bearer $TOKEN"

curl -X DELETE http://localhost:8000/api/v1/assets/$ASSET_ID \
  -H "Authorization: Bearer $TOKEN"
```

Storage keys (`products/{product_id}/{asset_id}/{safe_filename}`) are
server-generated and never returned by the API - only `filename` (the
sanitized name) and `original_filename` (the raw client-supplied name,
metadata only) are exposed.

**Known limitations:** magic-byte content verification only covers
PNG/JPEG/PDF/ZIP; other allowed types (SVG, JSON, fonts, STL) are
trusted by declared `Content-Type` alone. There is no virus/malware
scanning. Uploads are held fully in memory before being written to
storage (no streaming/chunked upload yet) - acceptable given the 25 MiB
default cap, but worth revisiting before raising it.

## LLM Gateway API (Sprint P5)

Every endpoint requires a valid access token. `POST /generate` requires
`operator` or `admin`; all others accept any active role.

| Method | Path                              | Role required    | Description |
|--------|-------------------------------------|-------------------|-------------|
| POST   | `/api/v1/llm/generate`             | operator, admin   | Send a chat-style request to a provider. `provider` optional (falls back to `LLM_DEFAULT_PROVIDER`); `response_format` optional (enforces JSON Schema validation on the result regardless of provider-native support). Persists an `llm_requests` row either way. |
| GET    | `/api/v1/llm/providers`            | any active role   | Lists `LLM_ALLOWED_PROVIDERS` with each one's `configured` (credentials present) flag and default model. |
| GET    | `/api/v1/llm/models`               | any active role   | Provider list plus configured model aliases (`LLM_MODEL_ALIASES`). |
| GET    | `/api/v1/llm/health`               | any active role   | Live health check per allowed provider - `configured` / `reachable` / `unavailable` / `not_configured`. Never 5xx's on a provider being down; the failure is reported in the body. |
| GET    | `/api/v1/llm/requests/{request_id}` | any active role  | Fetch one past request's metadata (routing/tokens/cost/latency/status) - never the prompt or generated content. 404 for unknown ids and for ids owned by another non-admin user (same code, to avoid enumeration). Admins can view any request. |

Example, using the network-free `fake` provider (no API key needed - set
`LLM_DEFAULT_PROVIDER=fake` or pass `"provider": "fake"` explicitly):

```bash
curl -X POST http://localhost:8000/api/v1/llm/generate \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"provider": "fake", "model": "fake-v1", "messages": [{"role": "user", "content": "hello"}]}'

curl http://localhost:8000/api/v1/llm/providers -H "Authorization: Bearer $TOKEN"
curl http://localhost:8000/api/v1/llm/health -H "Authorization: Bearer $TOKEN"
curl http://localhost:8000/api/v1/llm/requests/$REQUEST_ID -H "Authorization: Bearer $TOKEN"
```

**Known limitations:** streaming is implemented per-provider
(`LLMProvider.stream`) and exercised in tests, but is not exposed over
the HTTP API this sprint - `POST /generate` is request/response only.
The Anthropic adapter's `stream()` currently delegates to a single
non-streamed call rather than parsing real SSE, since no streaming
endpoint is exposed yet to need it. Cost estimates are only as accurate
as `LLM_PRICING_JSON`; an unpriced provider/model pair returns
`estimated_cost_usd: null` rather than a fabricated number. No prompt or
generated content is ever persisted - `llm_requests` stores routing,
token counts, cost, latency, and status only.

## Agent Run API (Sprint P6: AI Runtime; Sprint P8: async execution)

Every endpoint requires a valid access token. Creating/cancelling a run
require `operator` or `admin`; reads accept any active role, scoped to
the caller's own runs unless `admin`.

| Method | Path                          | Role required    | Description |
|--------|--------------------------------|-------------------|-------------|
| POST   | `/api/v1/runs`                | operator, admin   | Validate and **enqueue** a run against an `AgentDefinition` (`agent_id`, `user_input`, optional `context`). Returns `202 Accepted` with the run in its `queued` state - it does not execute here (Sprint P8; see Job Queue & Worker below). 404 unknown agent, 409 agent not `active`. |
| GET    | `/api/v1/runs/{id}`           | any active role   | Fetch one run's metadata (status/timestamps/duration/provider/model/tokens/cost/output/error) - never `user_input`/`context`. 404 for unknown or non-owned ids. |
| GET    | `/api/v1/runs`                | any active role   | Paginated list (`limit`, `offset`), scoped to the caller unless admin. |
| POST   | `/api/v1/runs/{id}/cancel`    | operator, admin   | Cancel a run. `queued`/`retrying` job: cancelled immediately (`200`). A worker has already claimed the job (`running`): `409` - an in-flight provider call cannot be interrupted. Already `completed`/`failed`/`cancelled`: `409`. |

Example, using an `AgentDefinition` seeded directly (there is no
`AgentDefinition` CRUD API - see Setup above) with
`configuration_json: {"system_prompt": "...", "model": "fake-v1"}`:

```bash
curl -X POST http://localhost:8000/api/v1/runs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"agent_id": "'"$AGENT_ID"'", "user_input": "hello"}'
# -> 202 Accepted, {"status": "queued", "output_text": null, ...}
# a running `worker` process picks it up shortly after - poll GET below

curl http://localhost:8000/api/v1/runs -H "Authorization: Bearer $TOKEN"
curl http://localhost:8000/api/v1/runs/$RUN_ID -H "Authorization: Bearer $TOKEN"
curl -X POST http://localhost:8000/api/v1/runs/$RUN_ID/cancel -H "Authorization: Bearer $TOKEN"
```

**Important architectural decisions:**

- **Execution moved out of the request/response cycle in Sprint P8.**
  `POST /runs` now only validates and enqueues (`app.jobs.service.
  enqueue_agent_run`, in the same transaction as the paired `Job` row -
  see Job Queue & Worker below); a separate `worker` process calls the
  exact same, unmodified `app.runtime.executor.execute_run` this
  endpoint used to call directly. `RUNNING -> CANCELLED` is a real,
  validated transition in the state machine (`app/runtime/models.py`),
  now reachable in practice too, just not for a job a worker has already
  claimed (see the cancel row above and the documented limitation in Job
  Queue & Worker).
- **`agent_runs` persists `output_text`; unlike `llm_requests` (P5), it
  does not persist `user_input`/`context`.** P5 deliberately excludes
  all prompt/response content. P6's own persistence requirements
  explicitly list "output" but not "user input" - so `output_text` is
  stored (a deliberate, spec-driven divergence from the P5 pattern) while
  the raw input that produced it is not (staying consistent with P5's
  security posture where the spec doesn't say otherwise). See
  `app/models/agent_run.py`'s module docstring.
- **Gateway/configuration failures are no longer visible in `POST
  /runs`'s own response (Sprint P8 change).** The run row is still
  persisted as `failed` either way (with `error_code`/`error_message`) -
  `GET /runs` will show it - but since execution now happens later, in
  the worker process, `POST /runs` itself can only ever return `202`, a
  `4xx` for an enqueue-time validation problem (unknown/inactive agent),
  or a genuine `5xx` if enqueueing itself fails. The LLMError-to-HTTP
  mapping `POST /llm/generate` uses is now applied inside the worker (as
  a job-level FAILED-not-retried outcome, not an HTTP status - there is
  no HTTP response left to map it onto by the time it happens).
- **No `AgentDefinition` CRUD API was added.** Definitions are still
  seeded directly (per Sprint P2's `AgentDefinitionRead` docstring); this
  sprint only adds the ability to *execute* one.
- **`RuntimeService` is a module of plain async functions**
  (`app/runtime/service.py`), not a class, despite the spec's own
  "Implement `RuntimeService`" wording - every other service in this
  codebase (`llm_service.py`, `asset_service.py`, `product_service.py`)
  already follows this shape, and "follow current project style
  exactly" wins over the spec's "suggested" module structure.

## Workflow Run API (Sprint P7: Workflow Orchestration; Sprint P8: async execution)

Every endpoint requires a valid access token. Creating/cancelling a run
require `operator` or `admin`; reads accept any active role, scoped to
the caller's own runs unless `admin` - identical matrix to the Agent Run
API above.

| Method | Path                                   | Role required    | Description |
|--------|-------------------------------------------|-------------------|-------------|
| POST   | `/api/v1/workflow-runs`                | operator, admin   | Validate and **enqueue** a run against a `WorkflowDefinition` (`workflow_id`, `user_input`, optional `context`) - persists the run plus one `pending` `WorkflowStepRun` per step. Returns `202 Accepted` with the run in its `queued` state (Sprint P8; see Job Queue & Worker below). 404 unknown workflow or unknown referenced agent, 409 workflow not `active` or referenced agent not `active`, 422 invalid workflow definition. |
| GET    | `/api/v1/workflow-runs/{id}`           | any active role   | Fetch one run's metadata (status/timestamps/duration/output/error) - never `user_input`/`context`. 404 for unknown or non-owned ids. |
| GET    | `/api/v1/workflow-runs`                | any active role   | Paginated list (`limit`, `offset`), scoped to the caller unless admin. |
| GET    | `/api/v1/workflow-runs/{id}/steps`     | any active role   | List that run's `WorkflowStepRun`s in execution order - each includes `input_snapshot`, `output_text`, `status`, `agent_run_id` (linking to the underlying P6 `AgentRun`), and error fields. 404 for unknown or non-owned run ids. |
| POST   | `/api/v1/workflow-runs/{id}/cancel`    | operator, admin   | Cancel a run. `queued`/`retrying` job: cancelled immediately (`200`), same as the Agent Run API. A worker has already claimed the job (`running`): a cooperative `cancel_requested_at` flag is set instead and the run is returned unchanged (still `200`, still `running`) - the worker checks this flag between workflow steps and stops there once it notices; a single in-flight provider call within a step still cannot be interrupted. Re-cancelling an already-`cancelled` run succeeds as a no-op; `409` if already `completed`/`failed`. |

Example, using a `WorkflowDefinition` seeded directly (there is no
`WorkflowDefinition` CRUD API - definitions are still seeded, same as
`AgentDefinition`) whose `definition_json` follows the convention in
`app/workflows/definition.py`:

```json
{
  "steps": [
    {
      "step_id": "research",
      "name": "Research",
      "agent_definition_id": "<uuid>",
      "order": 1
    },
    {
      "step_id": "summarize",
      "name": "Summarize",
      "agent_definition_id": "<uuid>",
      "order": 2,
      "input_mapping": {"source": "previous_output"}
    }
  ]
}
```

```bash
curl -X POST http://localhost:8000/api/v1/workflow-runs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"workflow_id": "'"$WORKFLOW_ID"'", "user_input": "hello"}'
# -> 202 Accepted, {"status": "queued", "output_text": null, ...}

curl http://localhost:8000/api/v1/workflow-runs -H "Authorization: Bearer $TOKEN"
curl http://localhost:8000/api/v1/workflow-runs/$RUN_ID -H "Authorization: Bearer $TOKEN"
curl http://localhost:8000/api/v1/workflow-runs/$RUN_ID/steps -H "Authorization: Bearer $TOKEN"
curl -X POST http://localhost:8000/api/v1/workflow-runs/$RUN_ID/cancel -H "Authorization: Bearer $TOKEN"
```

Example response body for `GET /workflow-runs/{id}/steps` once a worker
has picked up and finished the two-step example above:

```json
[
  {
    "id": "...", "step_id": "research", "step_order": 1,
    "status": "completed", "input_snapshot": "hello",
    "output_text": "[research output]", "agent_run_id": "..."
  },
  {
    "id": "...", "step_id": "summarize", "step_order": 2,
    "status": "completed", "input_snapshot": "[research output]",
    "output_text": "[summarize output]", "agent_run_id": "..."
  }
]
```

**Important architectural decisions:**

- **Execution moved out of the request/response cycle in Sprint P8,
  same as the Agent Run API.** `POST /workflow-runs` now only validates
  and enqueues (`app.jobs.service.enqueue_workflow_run`, in the same
  transaction as the paired `Job` row and every `pending`
  `WorkflowStepRun`); a separate `worker` process calls the exact same,
  unmodified `app.workflows.executor.execute_workflow_run` this endpoint
  used to call directly - now with an extra optional
  `cancellation_check` callback the worker supplies (see Job Queue &
  Worker below), which Sprint P7's synchronous flow never needed.
- **The executor never calls `app.llm` or `app.services.llm_service`
  directly.** Every step goes through `app.runtime.service.create_run` +
  `app.runtime.executor.execute_run` - the exact same P6 code path
  `POST /runs` uses - so every workflow step also produces a real P6
  `AgentRun` row (linked via `WorkflowStepRun.agent_run_id`) and, through
  that, a real P5 `llm_requests` audit row. "Do not bypass the P6
  runtime" / "The executor must not call provider adapters or the LLM
  Gateway directly" is enforced structurally, not just by convention.
- **`workflow_step_runs` persists `input_snapshot`; `workflow_runs` does
  not persist `user_input`/`context`.** The sprint spec explicitly lists
  "input snapshot" among the required per-step persistence fields
  (unlike P6's `agent_runs`, which never stores the caller's raw
  input) - so each step's actually-resolved input (built by
  `app/workflows/input_builder.py`) is stored, while the workflow-run-
  level raw input follows the same "no raw input" posture P6 already
  established. See `app/models/workflow_run.py` and
  `app/models/workflow_step_run.py`'s module docstrings.
- **On a step failure, later `pending` steps become `skipped`, not
  silently left `pending` forever.** "Do not continue executing later
  steps after a failure" (sprint section 6) - the failed step's own row
  becomes `failed`; every remaining `pending` `WorkflowStepRun` under the
  same run becomes `skipped` in the same commit that fails the
  `WorkflowRun` itself.
- **Cancelling an already-`cancelled` run is an idempotent no-op**
  (mirrors P4's soft-delete idempotency, not P6's stricter
  always-raise-on-terminal-state cancel) - handled as an early return in
  `app/workflows/service.py`'s `cancel_run`, before the transition table
  is even consulted, so the transition table itself stays free of a
  `CANCELLED -> CANCELLED` self-loop that would be wrong for every other
  caller.
- **The `input_mapping` vocabulary is closed and deterministic:**
  `workflow_input` (the run's own `user_input`), `previous_output` (the
  immediately preceding step's output), `step_output` (a named *earlier*
  step's output - forward references and self-references are rejected at
  definition-parse time), and `static` (a fixed string). There is no
  templating syntax, expression language, or arbitrary code execution -
  "No arbitrary template execution" (sprint constraint) is enforced by
  this vocabulary simply not containing anything else.
- **No `WorkflowDefinition` CRUD API was added**, mirroring P6's decision
  for `AgentDefinition` - definitions are still seeded directly; this
  sprint only adds the ability to *execute* one.

**Known limitation:** cancelling a workflow step's own single in-flight
LLM Gateway call is still not possible (same underlying limitation as
the Agent Run API) - the cooperative `cancel_requested_at` flag is only
checked *between* steps, not mid-step. See "Job Queue & Worker" below
for the full cancellation design.

## Job Queue & Worker (Sprint P8: Asynchronous Job Execution)

```
  client
    │
    │ POST /runs or /workflow-runs
    ▼
  backend (FastAPI)  ──validate + enqueue, one db.commit()──▶  jobs table (PostgreSQL)
    │                                                                │  ▲
    │ 202 Accepted, run status = queued                              │  │ SELECT ... FOR UPDATE
    ▼                                                                │  │ SKIP LOCKED (poll loop)
  client polls                                                       ▼  │
    GET /runs/{id}                                                worker (app/worker/)
    GET /workflow-runs/{id}/steps                                    │
        ▲                                                            │ calls, unchanged
        │  same AgentRun / WorkflowRun / WorkflowStepRun rows        ▼
        └───────────────────────────────────────────────  app.runtime.executor.execute_run
                                                             app.workflows.executor.execute_workflow_run
```

**Queue technology choice: the existing PostgreSQL database, via a
`jobs` table claimed with `SELECT ... FOR UPDATE SKIP LOCKED`** - not
Redis/RabbitMQ/Celery/arq, and not a hand-rolled message broker. The
sprint explicitly calls for "the smallest production-sensible queue
technology" and endorses "a durable database-backed job table with
worker polling" by name; introducing a second stateful service (a
broker) for a single-worker-process MVP would be the "over-engineering"
the same spec warns against. `SELECT ... FOR UPDATE SKIP LOCKED` is
natively supported by `asyncpg`/SQLAlchemy and guarantees at most one
worker can ever hold a given job `RUNNING` at a time, even with multiple
worker processes/replicas running concurrently.

**Enqueue safety - not a naive dual write, not a transactional
outbox.** `app.runtime.service.create_run` and
`app.workflows.service.create_workflow_run` both gained a
`commit: bool = True` parameter (default preserves every pre-P8
caller/test unchanged). `app.jobs.service.enqueue_agent_run` /
`enqueue_workflow_run` call them with `commit=False`, add the paired
`Job` row to the same session, and issue exactly **one** `db.commit()`
for both. A transactional outbox pattern (writing an outbox row plus a
separate relay process) would solve a problem that doesn't exist here:
the queue already lives in the same database as the run it enqueues, so
one transaction is sufficient by construction - there is no cross-system
boundary to bridge.

**Job lifecycle:** `queued -> running -> completed | failed | retrying |
cancelled`, `retrying -> running | cancelled` (see
`app/jobs/models.py`'s `JOB_TRANSITIONS`). Persisted per job:
`job_type` (`agent_run`/`workflow_run`), `target_run_id` (a polymorphic
reference to `agent_runs.id`/`workflow_runs.id`, disambiguated by
`job_type` - no FK, since it points at two different tables),
`attempt_count`/`max_attempts`, `next_attempt_at` (backoff), `heartbeat_at`/
`lease_expires_at`/`worker_id` (liveness), `cancel_requested_at`
(cooperative cancel signal), `error_code`/`error_message` (safe,
human-readable only - never a raw provider payload, stack trace, or
credential), and `input_json` (the caller's `user_input`/`context` - see
below). `output_json` stays unused/null; the `AgentRun`/`WorkflowRun` row
remains the single source of truth for output.

**A deliberate privacy divergence:** `Job.input_json` DOES persist the
caller's raw `user_input`/`context` - the first place in this codebase
that happens. P5/P6/P7 all avoid persisting raw prompts specifically
because the same request that creates a run also executes it, so the
input only ever needs to live in memory. Sprint P8 breaks that
assumption: the worker that executes a job is a *different process*,
possibly running much later, with no other way to know what to execute.
The sprint's own privacy carve-out ("no secrets, credentials, or raw
provider payloads") does not cover a caller's own operational request
content, and `Job.input_json` already existed for exactly this purpose
(see `app/models/job.py`'s docstring). **Known limitation:** there is no
TTL or cleanup policy for this data this sprint - a completed job's
`input_json` stays in the table indefinitely.

**Retry policy - bounded, and only for genuinely transient failures.**
`LLMError`/`RuntimeDomainError`/`WorkflowDomainError` (rate limits,
timeouts, invalid/inactive/misconfigured agents, invalid workflow
definitions, malformed input) are **never retried at the job level** -
by the time one of these reaches the worker, the underlying
`AgentRun`/`WorkflowRun` has already been persisted terminally `failed`
by the executor itself, so the run's own state machine no longer permits
re-execution (and `LLMError` specifically already passed through Sprint
P5's own gateway-level retry policy before ever surfacing this far).
Only a genuinely unexpected exception - a worker/infrastructure-level
bug, not a business-logic failure - triggers job-level retry, with
exponential backoff (`job_retry_backoff_base_seconds * 2^(attempt-1)`),
up to `job_max_attempts` (default 3) before the job is marked `failed`.

**Cancellation - three tiers, see `app/jobs/service.py`'s
`cancel_agent_run`/`cancel_workflow_run`:**
1. `queued`/`retrying` job: cancelled immediately, then the run itself is
   cancelled via the unchanged P6/P7 service-layer cancel. `200`.
2. `running` **agent-run** job: `409 JobAlreadyRunningError` - a single
   in-flight LLM Gateway call has no "between steps" checkpoint and
   cannot be safely interrupted.
3. `running` **workflow-run** job: `Job.cancel_requested_at` is set (a
   cooperative timestamp, not a status-field write, so it never races
   the worker's own concurrent status transitions on the same row) and
   the run is returned unchanged, still `200`. The worker's
   `execute_workflow_run` call receives an optional `cancellation_check`
   callback (polling this same flag) that's awaited before every step;
   once it returns `True`, the run becomes `cancelled` (not `failed`)
   and every remaining `pending` step becomes `cancelled` too (not
   `skipped` - that's reserved for "an earlier step failed"). Cancelling
   an already-`cancelled` run/job is idempotent throughout.

**Stale-job recovery.** Every `RUNNING` job carries a `lease_expires_at`
(default 120s, `JOB_LEASE_SECONDS`), renewed every
`job_heartbeat_interval_seconds` (default 15s) by a small background
task running *concurrently* with the worker's executor call (on its own
short-lived DB session, since `AsyncSession` isn't safe for concurrent
use from two coroutines at once) - so a long-running job never has its
own lease expire out from under it while a worker is still legitimately
working on it. `app.jobs.queue.recover_stale_jobs` runs once on worker
startup and periodically thereafter (every `max(job_lease_seconds, 30)`
seconds): it reclaims `RUNNING` jobs whose lease has already expired
(crash/stuck-worker recovery) back to `retrying` (if attempts remain) or
`failed` (if exhausted) - and, by its own `WHERE lease_expires_at < now`
filter plus `SKIP LOCKED`, it can never touch a job with a currently
valid lease, and never replays `completed`/`failed`/`cancelled` work
(only `RUNNING` jobs are ever eligible).

**Health reporting.** `GET /api/v1/health` now also reports `queue`
(mirrors `database` - the queue lives in the same PostgreSQL instance,
so there is no separate broker connectivity to check) and `worker`
(`available`/`unavailable`, based on whether any row in
`worker_heartbeats` - a separate table from `jobs`, answering "is any
worker process alive right now" independent of current workload - is
fresher than `worker_heartbeat_stale_after_seconds`, default 60s). The
`worker` Docker service has its own, separate `HEALTHCHECK`
(`app/worker/healthcheck.py`, a small synchronous script using
`psycopg2` directly) checking that *specific* worker process's own
heartbeat freshness - independent of the HTTP-level check, since the
worker container runs no HTTP server to poll.

**Running the worker locally (without Docker):**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
# requires a running PostgreSQL reachable per your .env, with migrations applied
python -m app.worker
```

**Known limitations (beyond the ones already called out above):** no
scheduled/cron jobs, no parallel workflow-step execution, no DAG
branching, no WebSocket/streaming progress updates, no human-approval
steps, and no Kubernetes-style orchestration - single-process polling
worker only this sprint (though safely horizontally-scalable to more
worker replicas, thanks to `SELECT ... FOR UPDATE SKIP LOCKED`, should
that become necessary before a real message broker is warranted).

## Local Development (without Docker)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# requires a running PostgreSQL reachable per your .env
uvicorn app.main:app --reload
```

## Tests

Tests run against an isolated database (`settings.test_database_url`,
defaults to `${POSTGRES_DB}_test`) - never the development database.
Create it once:

```bash
docker compose exec db createdb -U packverse packverse_test
```

Then run the suite:

```bash
docker compose exec backend pytest -v
# or, locally:
cd backend && pytest -v
```

Test files:

| File | Covers |
|------|--------|
| `tests/test_health.py` | `/` and `/api/v1/health` regression, plus (Sprint P8) `queue`/`worker` field reporting: fresh heartbeat -> `available`, no heartbeat / stale heartbeat -> `unavailable` |
| `tests/test_models.py` | ORM defaults, relationships, cascade delete, uniqueness constraints |
| `tests/test_products_api.py` | Product CRUD, pagination, 404, 409, validation errors (as an authenticated operator) |
| `tests/test_auth.py` | Registration, login, JWT issuance/expiry/signature checks, refresh token flow |
| `tests/test_authorization.py` | Product API's viewer/operator/admin access matrix, 401s, disabled accounts |
| `tests/test_migrations.py` | Alembic `upgrade head` / `downgrade base` / partial downgrades (to P7, to P6, to P5, to P4, to P2), one-step downgrade from head, table/column presence (including P8's `worker_heartbeats` and `jobs` queue columns), revision ids |
| `tests/test_config.py` | JWT secret policy (dev auto-generation, persistence, never overwriting, fail loudly outside dev) and storage settings (backend validation, S3 required-fields check, MIME allowlist parsing) |
| `tests/test_storage_local.py` | `LocalStorageBackend`: store/open/exists/delete/get_metadata, path-traversal rejection, atomic writes, idempotent delete, missing-object handling |
| `tests/test_storage_s3.py` | `S3StorageBackend` against a mocked boto3 client: put/get/delete/head/presigned URL, error-code mapping (`NoSuchKey`/`404` → not-found, other `ClientError`s → unavailable) |
| `tests/test_assets_api.py` | Asset API: upload (role matrix, validation errors, checksum, duplicate filenames, storage-rollback-on-DB-failure), list/pagination/soft-delete exclusion, download (headers, deleted, missing-object), delete (idempotency, storage-failure handling) |
| `tests/test_llm_gateway.py` | `LLMGateway`: provider/model routing (explicit, default, missing, unsupported, alias resolution), retry policy (retryable exhausts, non-retryable doesn't retry, timeout-then-succeed), response normalization, cost estimation (configured + unknown-returns-None), health check (never raises, reports `not_configured` for an allowed-but-uncredentialed provider), streaming |
| `tests/test_llm_fake_provider.py` | `FakeProvider`: deterministic content/usage, `fail_with` override, `response_content` override, health reachable/unavailable, streaming |
| `tests/test_llm_anthropic_adapter.py` | `AnthropicProvider` against `httpx_mock`: request mapping (headers, system/messages split, no API key in body), response/usage mapping, timeout/connection/rate-limit/auth/5xx error mapping, unexpected-response-shape handling, health check |
| `tests/test_llm_openai_adapter.py` | `OpenAICompatibleProvider` against `httpx_mock`: request mapping (auth header, org/project headers), structured-output request format, response/usage mapping, error mapping, health check |
| `tests/test_llm_structured_output.py` | Gateway-level JSON Schema validation: valid passthrough, malformed JSON, schema-violation, no-`response_format`-skips-validation, `raw_text` never leaks into the exception's own message, unsupported structured-output mode |
| `tests/test_llm_api.py` | `/api/v1/llm/*`: auth/role matrix on `/generate`, error-code mapping (429/504/503/422), `/providers`, `/models`, `/health`, `/requests/{id}` (owner, unknown-404, non-owner-404, admin-any), persistence (success/failure/tokens/latency/cost, never the prompt or content) |
| `tests/test_runtime_models.py` | `AgentRun` state machine (`app.runtime.models.validate_transition`): every valid transition, illegal transitions (queued->completed/failed, any transition out of a terminal state), the raised exception carries current/target |
| `tests/test_runtime_prompt_builder.py` | `build_generate_request`: required-config enforcement (`system_prompt`/`model`), optional provider/temperature/max_tokens forwarding, context rendering into the prompt, agent id/name in `metadata` |
| `tests/test_runtime_service.py` | `create_run`/`get_run`/`list_runs`/`cancel_run`: agent existence/active-status checks, ownership scoping (owner/non-owner/admin), pagination, cancel from `queued` and `running` (including duration computation), illegal-transition rejection from every terminal state |
| `tests/test_runtime_executor.py` | `execute_run`: successful execution (status/output/tokens/cost/`llm_request_id` linkage to the P5 audit trail), gateway failure/timeout (run marked `failed` and re-raised), misconfigured agent, executing a non-`queued` run, context reaching the provider through the prompt |
| `tests/test_runtime_api.py` | `/api/v1/runs/*`: auth/role matrix on `POST /runs` (Sprint P8: now asserts `202`/`queued`), enqueue-time error mapping (404/409; a misconfigured-but-active agent now still enqueues, `202`), retrieval (owner/non-owner-404/admin-any, never `user_input`/`context`), list pagination/ownership scoping, cancel (401/403/404, queued-cancel-succeeds-200, already-cancelled-409) |
| `tests/test_workflow_models.py` | `WorkflowRun`/`WorkflowStepRun` state machines (`app.workflows.models`): every valid transition for both, illegal transitions, every terminal state accepts no further transitions, raised exceptions carry current/target |
| `tests/test_workflow_definition.py` | `parse_workflow_steps`: valid ordered/out-of-order workflows, default mapping resolution, explicit `step_output`/`static`/`workflow_input` mappings, empty/missing/non-list steps, duplicate step ids/order, missing/malformed agent id, malformed step shape, forward-reference/self-reference/missing-step_id `step_output` rejection, unsupported mapping source, non-object `input_mapping`, `previous_output` on the first step |
| `tests/test_workflow_input_builder.py` | `build_step_input`: all four mapping sources, deterministic output for identical inputs, `previous_output` with no preceding step, missing `previous_output`/`step_output` references at runtime |
| `tests/test_workflow_service.py` | `create_workflow_run`/`get_run`/`list_runs`/`get_steps`/`cancel_run`/`get_active_workflow`: workflow existence/active-status checks, definition validation, referenced-agent existence/active-status checks, ownership scoping, pagination, step-run ordering, cancel from `queued` (cancels pending steps) and `running` (only cancels still-pending steps), idempotent re-cancel, illegal-transition rejection from `completed`/`failed` |
| `tests/test_workflow_executor.py` | `execute_workflow_run`: one-step and multi-step success (output propagation, named `step_output` references, final-output persistence), first-step and middle-step failure (remaining steps `skipped`), timeout/runtime-failure mapping, every step links to a real P6 `AgentRun`, workflow context reaching every step's prompt |
| `tests/test_workflow_run_api.py` | `/api/v1/workflow-runs/*`: auth/role matrix on `POST /workflow-runs` (Sprint P8: now asserts `202`/`queued`), enqueue-time error mapping (404/409/422), retrieval (owner/non-owner-404/admin-any, never `user_input`/`context`), list pagination/ownership scoping, step listing (owner/non-owner-404, steps stay `pending` until executed), cancel (401/403/404, queued-cancel-succeeds-200, already-cancelled-idempotent-200) |
| `tests/test_job_models.py` | `Job` state machine (`app.jobs.models.validate_job_transition`): every valid transition (including `retrying`), illegal transitions, every terminal state accepts no further transitions, raised exception carries current/target |
| `tests/test_job_queue.py` | `app.jobs.queue`: `claim_next_job` (oldest-first, skips not-yet-due `retrying` jobs, claims due ones, never claims `running`/terminal jobs, preserves `started_at` across retries), `renew_lease`, `mark_completed`/`mark_failed`/`mark_retrying`/`mark_failed_or_retry` (retry-vs-fail branching on `attempt_count`), `compute_backoff_seconds` (exponential), `mark_cancelled`/`cancel_queued_job`, `recover_stale_jobs` (never touches a valid lease, reclaims an expired one to `retrying` or `failed`, never replays `completed` work, idempotent) |
| `tests/test_job_service.py` | `app.jobs.service`: `enqueue_agent_run`/`enqueue_workflow_run` (atomic single-commit run+job creation, enqueue-time validation failures create neither row), `cancel_agent_run`/`cancel_workflow_run`'s full three-tier design (queued-cancels-both, running-agent-run-raises-`JobAlreadyRunningError`, running-workflow-run-sets-`cancel_requested_at`-idempotently, no-paired-job-falls-through-to-P6/P7-cancel-unchanged) |
| `tests/test_worker_dispatch.py` | `app.worker.dispatch.process_claimed_job`: agent/workflow success (proves reuse of the unmodified P6/P7 executors via provider/token/output fields only they set), domain failures (`LLMError`/`AgentConfigurationError`) fail the job immediately with no retry, a genuinely unexpected exception retries (and eventually fails once `max_attempts` is exhausted), duplicate delivery (target run already `completed`) skips re-execution, a `cancelled` target run marks the job `cancelled`, a workflow job honors `cancel_requested_at` between steps |
| `tests/test_worker_runner.py` | `app.worker.runner`: `default_worker_id` (env var vs. hostname fallback), `upsert_heartbeat` (insert then update), `process_one_job` (empty queue vs. real claim-and-complete), `_heartbeat_while_running` (extends the lease while "executing", stops once the job is no longer `running`), `run_worker`'s startup stale-job recovery pass and its own `worker_heartbeats` row |
| `tests/test_worker_healthcheck.py` | `app.worker.healthcheck.is_healthy`: no heartbeat row -> unhealthy, fresh heartbeat -> healthy, stale heartbeat -> unhealthy, unreachable database -> unhealthy (fails fast, not via TCP timeout) |

Each test gets its own database transaction (via `tests/conftest.py`'s
`db_session`/`client` fixtures) that is rolled back afterward, so tests
pass regardless of execution order and don't need to be run with
`-p no:randomly` or similar.

**Sandbox note:** the full suite (P1-P5) has since been run and passed
locally - 218 tests, 2 pre-existing Starlette deprecation warnings (not
failures), ruff clean, mypy clean across 64 source files, via the
repo-root `verify.sh` script (see below). See
[`docs/P1_LOCAL_VERIFICATION.md`](docs/P1_LOCAL_VERIFICATION.md) for
exact commands, expected output, the full verification history, and the
follow-up fixes each sprint's real local run caught (P4: `9214d51`,
`2d7a5e1`; P5: `b1751fe`, which also introduced `verify.sh`). Sprint P6,
P7, and P8's test files above were all written in the same sandbox (no
Docker, no network) and validated only via `python -m py_compile` plus a
manual unused-import/line-length sweep - none has yet been executed by
pytest; that's the next step, in Parts F, G, and H of
`docs/P1_LOCAL_VERIFICATION.md`. Per explicit CTO instruction, P7 was
implemented immediately after P6, and P8 immediately after P7, each time
without waiting for the prior sprint's real local verification first -
all three sprints' `./verify.sh` results are outstanding simultaneously
as of P8.

`verify.sh` (repo root) runs `docker compose ps`, `alembic current`,
`pytest -v`, `ruff check .`, `mypy app`, (Sprint P8) an explicit check
that the `worker` container's own Docker `HEALTHCHECK` reports
`healthy`, a `docker compose logs worker` tail for visibility, a `GET
/api/v1/health` check asserting `database`/`queue`/`worker` are all
`connected`/`connected`/`available`, and finally `git status --short` -
in sequence, stopping at the first failing step (`set -euo pipefail`),
and prints `ALL CHECKS PASSED` only on a genuine clean run:

```bash
./verify.sh
```

## Rules

- No hardcoded secrets - everything sensitive comes from environment
  variables via `.env` (never committed).
- Typed Python throughout; `mypy --strict` is configured in `pyproject.toml`.
- This repository does not modify the PackVerse OS Obsidian vault. The
  vault is the frozen specification; this repo is the implementation.

## Roadmap (per vault `10 Roadmap/Current Sprint.md`, adjusted per CTO instruction - see below)

1. Backend foundation - **P1, verified locally, CTO approved**
2. Database and domain models - **P2, verified locally, CTO approved**
3. Authentication & RBAC - **P3, verified locally, CTO approved**
4. Storage - **P4, verified locally, CTO approved**
5. LLM Gateway - **P5, verified locally, CTO approved**
6. AI Runtime - **P6, built, statically validated (`py_compile`), awaiting CTO local verification**
7. Workflow Orchestration - **P7, built, statically validated (`py_compile`), awaiting CTO local verification**
8. Asynchronous Job Execution - **P8, built, statically validated (`py_compile`), awaiting CTO local verification**
9. MCP Integration
10. RAG
11. Product Factory
12. Marketplace Automation
13. Deployment
14. MVP Launch

The vault's original item 7 was "MCP Integration" - per explicit CTO
instruction, Workflow Orchestration was moved ahead of it and implemented
as Sprint P7 instead (also noted in `app/models/workflow_definition.py`'s
docstring, which originally expected orchestration to start at a later
"Product Factory" sprint), and Asynchronous Job Execution was similarly
inserted as Sprint P8 - moving synchronous P6/P7 execution onto a durable
background worker before MCP/RAG/Product Factory build further on top of
it. MCP Integration and RAG shift down to items 9 and 10 accordingly. Per
explicit CTO instruction, P8 began immediately after P7 without waiting
for either P6's or P7's local verification first - do not begin Sprint
P9 until Sprints P6, P7, and P8 have all been verified locally and
approved.
