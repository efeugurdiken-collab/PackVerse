# Local Verification (P1-P4)

**Status: P1, P2, P3 verified locally and CTO-approved. P4 pending.**
Parts A and B below (P1/P2) and the CTO approval history for P3 are
historical record of runs already completed against a real PostgreSQL
instance. Part D (P4) has not been executed anywhere - this document
exists because the environment this code was written in cannot run it,
and the CTO instruction for every sprint in this repo has been explicit:
do not claim a sprint passed, and give exact reproducible steps instead.

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

## Acceptance (P1/P2)

Sprint P2 is only complete once every command in Part A and Part B has
been run against a real PostgreSQL instance and produced the expected
output above. This has since happened - see the CTO approval history
for the actual pasted output (20 passed, mypy clean on 32 source files).

## Part C — Sprint P3 (Authentication + RBAC)

Verified locally by the CTO after one reset-and-retry (an initial paste
of output was numerically identical to the pre-P3 baseline and was
correctly rejected as stale). The commands are the same shape as Part B
step 3/4 above, run after `docker compose down` + `docker compose up
--build -d` to guarantee a fresh container with the newly added
dependencies (`argon2-cffi`, `pyjwt`, `email-validator`) actually
installed:

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current   # -> 1f20f57819a3 (head)
docker compose exec backend pytest -v
docker compose exec backend ruff check .
docker compose exec backend mypy app
```

Confirmed result: `58 passed`, `mypy`: "Success: no issues found in 39
source files", `git status` clean at commit `ee9e360`.

## Part D — Sprint P4 (Storage Layer)

**Not yet run anywhere.** Same reset discipline as Part C applies: run
`docker compose down` then `docker compose up --build -d` first, so the
newly added `boto3`/`python-multipart` dependencies and the
`packverse_storage` volume actually exist before testing.

### 1. Migration

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current            # -> ae14cc314d2f (head)
docker compose exec backend alembic downgrade -1        # drops only the 7 P4 columns on assets
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade base
docker compose exec backend alembic upgrade head
```

Expected: `ae14cc314d2f (head)` after the first upgrade; the `downgrade
-1` step removes `original_filename`, `content_type`, `etag`,
`storage_backend`, `status`, `uploaded_by_user_id`, `deleted_at` from
`assets` while every P2/P3 table and column stays intact (spot-check
with `docker compose exec db psql -U packverse -d packverse -c '\d assets'`);
the final `downgrade base` / `upgrade head` pair proves the whole chain
still runs cleanly end to end.

### 2. Full test suite

```bash
docker compose exec backend pytest -v
```

Expected: every test in `test_storage_local.py`, `test_storage_s3.py`,
and `test_assets_api.py` passes, plus the storage-related additions to
`test_config.py` and the P4 additions to `test_migrations.py`, on top of
all P1-P3 tests continuing to pass unmodified (regression). Roughly 58
(P1-P3 baseline) + ~55 new P4 tests.

### 3. Lint and type checks

```bash
docker compose exec backend ruff check .
docker compose exec backend mypy app
```

Expected: `ruff check` → "All checks passed!"; `mypy app` → "Success: no
issues found in N source files" where N is larger than P3's 39 (new
files: `app/storage/{__init__,base,exceptions,local,s3,factory}.py`,
`app/models/asset.py` changes, `app/schemas/asset.py`,
`app/services/asset_service.py`, `app/api/v1/assets.py`).

### 4. Manual smoke test (optional but recommended)

```bash
# register + login to get a token, or reuse one from P3 testing
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"..."}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

PRODUCT_ID=$(curl -s -X POST http://localhost:8000/api/v1/products \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"slug":"smoke-test","title":"Smoke Test","product_type":"svg_pack","price_cents":0,"currency":"USD","metadata_json":{}}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

curl -X POST http://localhost:8000/api/v1/products/$PRODUCT_ID/assets \
  -H "Authorization: Bearer $TOKEN" -F "file=@./some-test-file.png"

ls backend/data/storage/products/  # confirm the file actually landed on disk
```

### If it fails

- **`alembic downgrade -1` errors on dropping the FK constraint**: check
  that the constraint name in the downgrade matches exactly what the
  upgrade created (`fk_assets_uploaded_by_user_id_users`) - PostgreSQL
  is case-sensitive here and a typo would fail loudly, not silently.
- **Uploads succeed but `docker compose exec backend ls data/storage`
  shows nothing**: confirm the `packverse_storage` named volume is
  actually mounted at `/app/data/storage` per `docker-compose.yml`, and
  that `STORAGE_LOCAL_ROOT` wasn't overridden to point somewhere else.
- **`test_assets_api.py` failures around the `client`/`storage_backend`
  fixtures**: this is the one piece of P4 test infrastructure that could
  not be exercised at all in the sandbox (no pytest-asyncio, no real
  filesystem writes were verified) - treat failures here as a
  legitimate implementation issue to report, not a fixture typo to
  silently patch around.
- **`mypy` failures in `app/storage/s3.py`**: confirm `boto3-stubs[s3]`
  actually installed (`pip show boto3-stubs` inside the container) -
  without it, `boto3.client("s3")` has no useful stub and mypy may
  report spurious `Any`-related strict-mode errors.

## Acceptance (P4)

Sprint P4 is only complete once every command in Part D has been run
against a real PostgreSQL instance and object storage volume, and
produced the expected output above. Until then, treat all P4 code as
**unverified**. Per CTO instruction: do not start Sprint P5 (LLM
Gateway) until this verification passes and is explicitly approved.
