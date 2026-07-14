# P1 + P2 Local Verification

**Status: Pending.** Nothing in this repository has been executed. This
document exists because the environment this code was written in cannot
run it, and the CTO instruction for this sprint was explicit: do not
claim P1 passed, and give exact reproducible steps instead.

## Why verification could not run here

The environment used to write Sprint P1 and P2 has:

- No Docker binary and no access to the Docker Hub registry (`docker`
  command not found; registry pulls return `403 blocked-by-allowlist`).
- No outbound network access to PyPI (`pip install` returns `403
  blocked-by-allowlist`), so no Python dependency in `pyproject.toml` -
  not even `fastapi` or `sqlalchemy` themselves - could be installed.
- No pre-installed copies of any of these packages.
- No sudo/apt access to work around the above.

Given that, **zero tests could be executed**, not only the ones that
need PostgreSQL. This applies equally to P1 (`/health` endpoint) and P2
(models, migration, Product API). What *was* done instead, as a partial
substitute:

- Every `.py` file was checked with `python -m py_compile` (catches
  syntax errors, not logic errors).
- Code was manually reviewed against the SQLAlchemy 2.x / Pydantic v2 /
  FastAPI APIs as documented, including the specific "join session to
  external transaction" pattern used in `tests/conftest.py`.
- The Alembic migration was hand-written directly from the SQLAlchemy
  models (autogeneration needs a live database diff, which wasn't
  available) and cross-checked field-by-field against `app/models/`.

None of this substitutes for actually running the code. Please run the
steps below before treating P1 or P2 as done.

## Part A — Verify Sprint P1

Run from the repository root (`packverse-platform/`):

```bash
cp .env.example .env
# edit .env and set a real POSTGRES_PASSWORD

docker compose config
docker compose up --build -d
docker compose ps
curl -i http://localhost:8000/health
curl -i http://localhost:8000/api/v1/health
docker compose logs --no-color backend
docker compose logs --no-color db
docker compose exec backend alembic current
```

Note: the root path is `/` (returns `{"status": "running", ...}`); the
health check used by Docker's healthcheck and by the acceptance
criteria is `/api/v1/health`. Run both `curl` commands above.

### Expected output

- `docker compose config` prints the resolved compose file with no
  errors.
- `docker compose ps` shows both `db` and `backend` as `Up` /
  `healthy`.
- `curl -i http://localhost:8000/` → `HTTP/1.1 200 OK`, body like
  `{"status":"running","service":"PackVerse Platform"}` (exact keys per
  `app/main.py`).
- `curl -i http://localhost:8000/api/v1/health` → `HTTP/1.1 200 OK`,
  body `{"status":"ok","database":"connected"}`.
- `docker compose logs backend` shows Uvicorn startup lines and no
  traceback.
- `alembic current` prints nothing yet (no migration has been applied)
  or an error about not being able to find a revision - this is
  expected before Part B's migration is applied; it should NOT show a
  connection error.

### If it fails

1. `docker compose logs backend` / `docker compose logs db` for the
   actual error.
2. Common causes: `.env` not copied/edited, port 5432 or 8000 already
   in use locally, Docker Desktop not running, `POSTGRES_PASSWORD` left
   as the placeholder.
3. Fix only P1 infrastructure files (`docker-compose.yml`,
   `backend/Dockerfile`, `backend/app/core/config.py`,
   `backend/app/database/session.py`) - do not touch P2 domain code to
   fix a P1 infrastructure problem.
4. Repeat the commands above until all conditions pass before moving to
   Part B.

## Part B — Verify Sprint P2

### 1. Migration

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current
docker compose exec backend alembic downgrade base
docker compose exec backend alembic upgrade head
```

Expected: `alembic upgrade head` completes with no errors and creates
five tables (`products`, `assets`, `jobs`, `agent_definitions`,
`workflow_definitions`); `alembic current` prints
`06b17a0f30ad (head)`; `downgrade base` removes all five tables cleanly
(verify with `docker compose exec db psql -U packverse -d packverse -c '\dt'`
showing none of the five tables); the final `upgrade head` succeeds
again from a clean slate.

### 2. Test database

```bash
docker compose exec db createdb -U packverse packverse_test
```

Expected: no output on success. If it already exists:
`createdb: error: database creation failed: ERROR: database "packverse_test" already exists` -
safe to ignore.

### 3. Full test suite

```bash
docker compose exec backend pytest -v
```

### Expected output

All tests in `tests/test_health.py`, `tests/test_models.py`,
`tests/test_products_api.py`, and `tests/test_migrations.py` pass -
approximately 20 tests, 0 failures. Example of a healthy final line:

```
======================= 20 passed in 4.32s ========================
```

### 4. Lint and type checks

```bash
docker compose exec backend ruff check .
docker compose exec backend mypy app
```

Expected: `ruff check` reports "All checks passed!"; `mypy app` reports
"Success: no issues found in N source files". `mypy --strict` is
configured in `pyproject.toml`, so this is a strict run - if it fails
on a forward-reference or generic-type issue, that's a real gap to fix
in the flagged file, not a config problem to loosen.

### If it fails

- **`alembic upgrade head` errors on enum/type creation**: check
  `docker compose logs db` for a stale `packverse` database from a
  previous partial run; `docker compose down -v` to reset the volume
  and start clean, then retry Part A and Part B in order.
- **`pytest` can't connect to `packverse_test`**: confirm step 2 above
  ran successfully and that `TEST_POSTGRES_DB` (if set in `.env`)
  matches the database name you created.
- **`pytest` fails inside `tests/conftest.py`'s `db_session` fixture**
  (e.g. `AttributeError` or `InvalidRequestError` around the SAVEPOINT
  restart logic): this is the one piece of P2 code that could not be
  exercised at all in the sandbox and is the most likely spot for a
  real bug - please treat failures here as a legitimate implementation
  issue to report, not a flaky test to retry.
- **Individual `test_products_api.py` failures**: run
  `pytest tests/test_products_api.py -v -k <test_name>` in isolation
  and check the response body via `-s` (pytest doesn't capture stdout
  by default when combined with `-s`) to see the actual FastAPI
  validation error detail.
- **`mypy` failures around `Mapped["Product"]` / `Mapped["Asset"]`
  forward references** in `app/models/product.py` or
  `app/models/asset.py`: confirm both files still have
  `from __future__ import annotations` at the top - removing it while
  editing would break the `TYPE_CHECKING`-only import pattern used
  there.

## Acceptance

Sprint P2 is only complete once every command in Part A and Part B has
been run against a real PostgreSQL instance and produced the expected
output above. Until then, treat all P1 and P2 code as **unverified**.
