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

> P1 and P2 were built in a sandboxed environment with no Docker and no
> external network access, then verified locally by the maintainer -
> Docker, PostgreSQL, the health endpoint, Alembic upgrade/downgrade,
> pytest (20 passed), ruff, and mypy all passed. Sprint P3 was written
> the same way (statically validated via `python -m py_compile`, not
> executed) and needs the same local verification pass before merging -
> see [`docs/P1_LOCAL_VERIFICATION.md`](docs/P1_LOCAL_VERIFICATION.md).

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
│   │   ├── services/        # business logic (product_service.py, user_service.py)
│   │   ├── agents/          # Agent runtime implementations (empty until P6)
│   │   ├── workflows/       # Workflow runtime implementations (empty until P9)
│   │   └── main.py         # FastAPI app entrypoint
│   ├── tests/                # model, API, auth, authorization, migration, and health tests
│   ├── alembic/
│   │   └── versions/
│   │       ├── 06b17a0f30ad_create_domain_tables.py   # P2 schema baseline
│   │       └── 1f20f57819a3_create_users_table.py     # P3: users table
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

Both migrations (`06b17a0f30ad_create_domain_tables.py` for P2,
`1f20f57819a3_create_users_table.py` for P3) were written by hand rather
than autogenerated - the sandbox this repo was built in has no
PostgreSQL instance to diff against. Verify the full chain locally:

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current          # -> 1f20f57819a3 (head)
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
| `tests/test_migrations.py` | Alembic `upgrade head` / `downgrade base` / partial downgrade to P2, table presence, revision ids |
| `tests/test_config.py` | JWT secret policy: dev auto-generation, persistence to `.env`, never overwriting, production/staging/test failing loudly without one |

Each test gets its own database transaction (via `tests/conftest.py`'s
`db_session`/`client` fixtures) that is rolled back afterward, so tests
pass regardless of execution order and don't need to be run with
`-p no:randomly` or similar.

**Sandbox note:** the P1/P2 portions of this suite have since been run
and passed locally (20 tests, see the CTO approval history). The Sprint
P3 additions (`test_auth.py`, `test_authorization.py`, `test_config.py`,
and the P3 portions of `test_migrations.py`) were written and statically
validated (`python -m py_compile`) the same way P1/P2 originally were -
in an environment with no Docker and no network access - so they have not
actually been executed yet. See
[`docs/P1_LOCAL_VERIFICATION.md`](docs/P1_LOCAL_VERIFICATION.md) for
exact commands, expected output, and troubleshooting.

## Rules

- No hardcoded secrets - everything sensitive comes from environment
  variables via `.env` (never committed).
- Typed Python throughout; `mypy --strict` is configured in `pyproject.toml`.
- This repository does not modify the PackVerse OS Obsidian vault. The
  vault is the frozen specification; this repo is the implementation.

## Roadmap (per vault `10 Roadmap/Current Sprint.md`)

1. Backend foundation - **P1, verified locally, CTO approved**
2. Database and domain models - **P2, verified locally, CTO approved**
3. Authentication & RBAC - **P3, built, local verification pending**
4. Storage
5. LLM Gateway
6. AI Runtime
7. MCP Integration
8. RAG
9. Product Factory
10. Marketplace Automation
11. Deployment
12. MVP Launch

Per CTO instruction, Sprint P4 (Storage) does not begin until P3 is
verified locally and approved.
