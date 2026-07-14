# PackVerse Platform

The runtime implementation of PackVerse OS, whose specification lives in a
separate, frozen Obsidian vault (`00 Company` through `10 Roadmap`). This
repository is the codebase; the vault is the spec. Do not merge them.

**Sprint P1 scope:** infrastructure foundation only. No AI features, no
Agents, no MCP, no RAG yet - those arrive in later sprints per the vault's
`10 Roadmap/Current Sprint.md` implementation order.

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
│   │   ├── api/           # FastAPI routers (versioned under v1/)
│   │   ├── core/           # config, logging
│   │   ├── database/        # SQLAlchemy engine/session
│   │   ├── models/          # ORM models (empty in P1 - no domain tables yet)
│   │   ├── services/        # business logic (empty in P1)
│   │   ├── agents/          # Agent runtime implementations (empty in P1)
│   │   ├── workflows/       # Workflow runtime implementations (empty in P1)
│   │   └── main.py         # FastAPI app entrypoint
│   ├── tests/
│   ├── alembic/             # migration environment
│   ├── Dockerfile
│   └── pyproject.toml
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md
```

## Setup

### 1. Configure environment

```bash
cp .env.example .env
# edit .env and set a real POSTGRES_PASSWORD
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
app itself connects to.

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

```bash
docker compose exec backend pytest
# or, locally:
cd backend && pytest
```

## Rules

- No hardcoded secrets - everything sensitive comes from environment
  variables via `.env` (never committed).
- Typed Python throughout; `mypy --strict` is configured in `pyproject.toml`.
- This repository does not modify the PackVerse OS Obsidian vault. The
  vault is the frozen specification; this repo is the implementation.

## Roadmap (per vault `10 Roadmap/Current Sprint.md`)

1. **Backend foundation** (this sprint - P1)
2. PostgreSQL database (included in this sprint's docker-compose)
3. Authentication
4. Storage
5. LLM Gateway
6. AI Runtime
7. MCP Integration
8. RAG
9. Product Factory
10. Marketplace Automation
11. Deployment
12. MVP Launch
