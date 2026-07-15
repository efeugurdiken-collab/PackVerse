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
Still no Agents runtime, MCP, RAG, or tool calling.

> P1-P5 were all written in a sandboxed environment with no Docker and
> no external network access, then verified locally by the maintainer.
> P1/P2: pytest (20 passed), ruff, mypy all passed. P3: 58 passed, mypy
> clean across 39 source files. P4: 129 passed (2 pre-existing Starlette
> deprecation warnings, not failures), ruff clean, mypy clean across 47
> source files - after two follow-up fixes caught by real local runs (a
> missing `pathlib` import, and a `MissingGreenlet`/SQLAlchemy
> identity-map bug in one rollback test). P5 was written and statically
> validated (`python -m py_compile`, not executed) the same way, in an
> environment with no Docker and no network access, and needs the same
> local verification pass before merging. See
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
│   │   ├── agents/          # Agent runtime implementations (empty until P6)
│   │   ├── workflows/       # Workflow runtime implementations (empty until P9)
│   │   └── main.py         # FastAPI app entrypoint
│   ├── tests/                # model, API, auth, authorization, storage, asset, LLM gateway, migration, and health tests
│   ├── alembic/
│   │   └── versions/
│   │       ├── 06b17a0f30ad_create_domain_tables.py   # P2 schema baseline
│   │       ├── 1f20f57819a3_create_users_table.py     # P3: users table
│   │       ├── ae14cc314d2f_extend_assets_for_storage.py  # P4: storage columns on assets
│   │       └── 7c19e4b8a2d6_create_llm_requests_table.py  # P5: llm_requests table
│   ├── Dockerfile
│   └── pyproject.toml
├── docker-compose.yml
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

### 2. Start the stack

```bash
docker compose up --build
```

This starts PostgreSQL and the FastAPI backend. The backend waits for the
database's healthcheck to pass before starting.

### 3. Verify

```bash
curl http://localhost:8000/
curl http://localhost:8000/api/v1/health
```

`/api/v1/health` returns `{"status": "ok", "database": "connected"}` once
both services are healthy.

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

All four migrations (`06b17a0f30ad_create_domain_tables.py` for P2,
`1f20f57819a3_create_users_table.py` for P3,
`ae14cc314d2f_extend_assets_for_storage.py` for P4,
`7c19e4b8a2d6_create_llm_requests_table.py` for P5) were written by hand
rather than autogenerated - the sandbox this repo was built in has no
PostgreSQL instance to diff against. Verify the full chain locally:

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current          # -> 7c19e4b8a2d6 (head)
docker compose exec backend alembic downgrade -1      # drops the llm_requests table only
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
| `tests/test_health.py` | `/` and `/api/v1/health` regression |
| `tests/test_models.py` | ORM defaults, relationships, cascade delete, uniqueness constraints |
| `tests/test_products_api.py` | Product CRUD, pagination, 404, 409, validation errors (as an authenticated operator) |
| `tests/test_auth.py` | Registration, login, JWT issuance/expiry/signature checks, refresh token flow |
| `tests/test_authorization.py` | Product API's viewer/operator/admin access matrix, 401s, disabled accounts |
| `tests/test_migrations.py` | Alembic `upgrade head` / `downgrade base` / partial downgrades (to P3, to P2), table/column presence, revision ids |
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

Each test gets its own database transaction (via `tests/conftest.py`'s
`db_session`/`client` fixtures) that is rolled back afterward, so tests
pass regardless of execution order and don't need to be run with
`-p no:randomly` or similar.

**Sandbox note:** the full suite (P1-P4) has since been run and passed
locally - 129 tests, 2 pre-existing Starlette deprecation warnings (not
failures), ruff clean, mypy clean across 47 source files. See
[`docs/P1_LOCAL_VERIFICATION.md`](docs/P1_LOCAL_VERIFICATION.md) for
exact commands, expected output, the full verification history, and the
two follow-up fixes (`9214d51`, `2d7a5e1`) that P4's real local run
caught. Sprint P5's test files above were written in the same sandbox
(no Docker, no network) and validated only via `python -m py_compile` -
they have not yet been executed by pytest; that's the next step, in
Part E of `docs/P1_LOCAL_VERIFICATION.md`.

## Rules

- No hardcoded secrets - everything sensitive comes from environment
  variables via `.env` (never committed).
- Typed Python throughout; `mypy --strict` is configured in `pyproject.toml`.
- This repository does not modify the PackVerse OS Obsidian vault. The
  vault is the frozen specification; this repo is the implementation.

## Roadmap (per vault `10 Roadmap/Current Sprint.md`)

1. Backend foundation - **P1, verified locally, CTO approved**
2. Database and domain models - **P2, verified locally, CTO approved**
3. Authentication & RBAC - **P3, verified locally, CTO approved**
4. Storage - **P4, verified locally, CTO approved**
5. LLM Gateway - **P5, built, statically validated (`py_compile`), awaiting CTO local verification and approval to start P6**
6. AI Runtime
7. MCP Integration
8. RAG
9. Product Factory
10. Marketplace Automation
11. Deployment
12. MVP Launch

Per CTO instruction, Sprint P6 (AI Runtime) does not begin until P5 is
verified locally and approved.
