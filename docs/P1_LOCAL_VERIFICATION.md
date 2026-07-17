# Local Verification (P1-P8)

**Status: P1, P2, P3, P4, P5 verified locally. P6, P7, and P8 not yet
verified.** Parts A, B, D, and E below are historical record of runs
already completed against a real PostgreSQL instance (P4 required two
follow-up fixes - a missing `pathlib` import and a `MissingGreenlet`/
identity-map test bug; P5 required two more - an invalid `noqa`
directive and two stale/inverted migration-test assertions - all four
resolved and re-verified). Part F is the reproduction guide for Sprint
P6; Part G is the reproduction guide for Sprint P7; Part H is the
reproduction guide for Sprint P8 - all three await their first real
local run. Per explicit CTO instruction, P7 was implemented immediately
after P6 without waiting for P6's own verification, and P8 was
implemented immediately after P7 without waiting for either P6's or
P7's, so all three are outstanding simultaneously - Part G's steps
assume Part F's migration/upgrade steps have already been run (P7's
migration chains directly off P6's), and Part H's steps assume both
Parts F and G's migration/upgrade steps have already been run (P8's
migration chains directly off P7's). This document exists because the
environment this code was written in cannot run it, and the CTO
instruction for every sprint in this repo has been explicit: do not
claim a sprint passed, and give exact reproducible steps instead.

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

**Verified.** Final confirmed result: `129 passed, 2 warnings` (pytest),
`All checks passed!` (ruff), `Success: no issues found in 47 source
files` (mypy). The two warnings are pre-existing Starlette deprecation
notices (`HTTP_422_UNPROCESSABLE_ENTITY` / `HTTP_413_REQUEST_ENTITY_TOO_LARGE`
renamed upstream) - not failures, not introduced by this sprint.

Two issues were found and fixed during verification before this final
pass: a missing `from pathlib import Path` in `tests/conftest.py`
(commit `9214d51`), and a `MissingGreenlet`/SQLAlchemy-identity-map bug
in `test_database_rollback_removes_stored_object` caused by reading an
ORM attribute after the service layer's own rollback expired it -
fixed in the test only, not the service layer, which was already
correct (commit `2d7a5e1`).

Same reset discipline as Part C applies: run
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

All commands in Part D have been run against a real PostgreSQL instance
and object storage volume and produced the expected output (`129
passed`, ruff clean, mypy clean on 47 source files). Per CTO
instruction: Sprint P5 (LLM Gateway) still does not start until this is
explicitly approved. (It has since been approved; see Part E below.)

## Part E — Sprint P5 (LLM Gateway)

**Verified.** Final confirmed result (via `verify.sh`, added during this
sprint's verification round): `218 passed, 2 warnings` (pytest, same 2
pre-existing Starlette deprecation notices as P4), `All checks passed!`
(ruff), `Success: no issues found in 64 source files` (mypy), commit
`b1751fe`.

Two issues were found and fixed during verification before this final
pass: an invalid `# noqa: broad on purpose - see docstring` directive in
`app/llm/gateway.py` (ruff flagged it as malformed - `noqa` needs an
actual rule code, not free text) fixed to a plain comment; and two
stale/inverted assertions in `tests/test_migrations.py`'s downgrade
tests, both root-caused from real pytest tracebacks rather than
guessed - `test_migration_downgrade_to_p2_preserves_domain_tables`
wasn't excluding the newly-added `llm_requests` table from its
post-downgrade-to-P2 expectation, and
`test_migration_downgrade_to_p4_removes_only_p4_columns` had an
inverted `isdisjoint` check left over from renaming an earlier
"downgrade one step FROM P4" test into a "downgrade TO P4" test - it
was asserting P4's own columns should be *absent* at a revision where
P4 is still fully applied. No migration files needed changes for
either fix - both bugs were in test expectations only.

Same reset discipline as Parts C/D applies: run `docker compose down`
then `docker compose up --build -d` first, so the newly added `httpx`,
`jsonschema`, and `pytest-httpx`/`types-jsonschema` dependencies are
actually installed before testing.

Same reset discipline as Parts C/D applies: run `docker compose down`
then `docker compose up --build -d` first, so the newly added `httpx`,
`jsonschema`, and `pytest-httpx`/`types-jsonschema` dependencies are
actually installed before testing.

### 1. Migration

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current            # -> 7c19e4b8a2d6 (head)
docker compose exec backend alembic downgrade -1        # drops only the llm_requests table
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade ae14cc314d2f   # back to P4 head
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade base
docker compose exec backend alembic upgrade head
```

Expected: `7c19e4b8a2d6 (head)` after the first upgrade; the `downgrade
-1` step removes only the `llm_requests` table while every P2/P3/P4
table and column stays intact (spot-check with
`docker compose exec db psql -U packverse -d packverse -c '\dt'` -
`llm_requests` should be the only table to disappear, and
`docker compose exec db psql -U packverse -d packverse -c '\d assets'`
should still show all P4 storage columns); the final `downgrade base` /
`upgrade head` pair proves the whole four-migration chain still runs
cleanly end to end.

### 2. Full test suite

```bash
docker compose exec backend pytest -v
```

Expected: every test in `test_llm_gateway.py`, `test_llm_fake_provider.py`,
`test_llm_anthropic_adapter.py`, `test_llm_openai_adapter.py`,
`test_llm_structured_output.py`, and `test_llm_api.py` passes, plus the
new LLM-related additions to `test_config.py` and the P5 additions to
`test_migrations.py`, on top of all P1-P4 tests continuing to pass
unmodified (regression). Roughly 129 (P1-P4 baseline) + ~76 new P5
tests. No test in this sprint makes a real network call - the
Anthropic/OpenAI adapter tests use `pytest-httpx`'s `httpx_mock` fixture
to mock HTTP responses, and the API-level tests route exclusively
through the `fake` provider via `app.dependency_overrides`.

### 3. Lint and type checks

```bash
docker compose exec backend ruff check .
docker compose exec backend mypy app
```

Expected: `ruff check` → "All checks passed!"; `mypy app` → "Success: no
issues found in N source files" where N is larger than P4's 47 (new
files: `app/llm/{__init__,base,models,exceptions,gateway,routing,pricing,factory}.py`,
`app/llm/providers/{__init__,_shared,fake,anthropic,openai_compatible}.py`,
`app/models/llm_request.py`, `app/schemas/llm.py`,
`app/services/llm_service.py`, `app/api/v1/llm.py`).

### 4. Manual smoke test (optional but recommended, no API key needed)

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"..."}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# Uses the network-free fake provider - works even with no ANTHROPIC_API_KEY
# or OPENAI_API_KEY set, as long as LLM_ALLOWED_PROVIDERS includes "fake"
# (it does by default).
curl -s -X POST http://localhost:8000/api/v1/llm/generate \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"provider":"fake","model":"fake-v1","messages":[{"role":"user","content":"hello"}]}'

curl -s http://localhost:8000/api/v1/llm/providers -H "Authorization: Bearer $TOKEN"
curl -s http://localhost:8000/api/v1/llm/health -H "Authorization: Bearer $TOKEN"

# To smoke-test a real provider instead, set ANTHROPIC_API_KEY in .env,
# restart the backend, and pass "provider":"anthropic" above.
```

Expected: the `/generate` call returns `200` with a `content` field
(the fake provider's deterministic echo of the last user message) and
`estimated_cost_usd: null` (no pricing configured for `fake:fake-v1` by
default); `/providers` lists `fake` with `"configured": true`;
`/health` reports `"status": "reachable"` for `fake`.

### If it fails

- **`alembic downgrade -1` from head errors on dropping the FK
  constraint**: check that the constraint name matches exactly what the
  upgrade created (`fk_llm_requests_user_id_users`) - PostgreSQL is
  case-sensitive here.
- **`test_llm_anthropic_adapter.py` / `test_llm_openai_adapter.py`
  failures**: these tests never hit the real network - if they're
  attempting real HTTP calls, `pytest-httpx` likely isn't installed or
  the `httpx_mock` fixture isn't being picked up; confirm
  `pip show pytest-httpx` inside the container.
- **`test_llm_api.py` failures around dependency overrides**: confirm
  `app.dependency_overrides[get_settings]` and
  `app.dependency_overrides[get_llm_gateway]` are both being cleared
  between tests (check `conftest.py`'s override-cleanup fixture) - a
  leaked override from one test can make an unrelated test silently use
  the wrong gateway.
- **`mypy` failures in `app/llm/providers/anthropic.py` or
  `openai_compatible.py`**: confirm `httpx` and `jsonschema`'s type
  stubs resolve cleanly; `types-jsonschema` must be installed (dev
  extra) for `jsonschema.validate`'s signature to type-check under
  `mypy --strict`.
- **A provider health check test hangs instead of failing fast**:
  confirm `LLM_TIMEOUT_SECONDS` is a small value in the test
  environment (tests construct their own `Settings` instances, so this
  should not depend on `.env`, but worth ruling out if a health-check
  test is unexpectedly slow).

## Acceptance (P5)

All commands in Part E have been run against a real PostgreSQL instance
and produced the expected output (`218 passed`, ruff clean, mypy clean
on 64 source files), via `verify.sh`. Per CTO instruction: Sprint P6 (AI
Runtime) still does not start until this is explicitly approved. (It has
since been approved; see Part F below.)

## Part F — Sprint P6 (AI Runtime)

**Not yet verified.** Written and statically validated
(`python -m py_compile` across `app/` and `tests/`, no runtime
execution) in the same no-Docker, no-network sandbox as every prior
sprint. Needs a real local run before it can be marked verified - same
discipline as every prior part: don't accept a claimed pass without
actual pasted command output, cross-checked numerically against the P5
baseline (218 passed / 64 mypy-clean source files) before trusting a
delta.

Same reset discipline as prior parts applies: run `docker compose down`
then `docker compose up --build -d` first, so the container picks up
the new `app/runtime/` package and `agent_runs` migration cleanly. No
new third-party dependencies were added this sprint.

### 1. Migration

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current            # -> a1c8f7d2b3e9 (head)
docker compose exec backend alembic downgrade -1        # drops only the agent_runs table
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade 7c19e4b8a2d6   # back to P5 head
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade base
docker compose exec backend alembic upgrade head
```

Expected: `a1c8f7d2b3e9 (head)` after the first upgrade; the `downgrade
-1` step removes only the `agent_runs` table while every P2-P5 table and
column stays intact (spot-check with
`docker compose exec db psql -U packverse -d packverse -c '\dt'` -
`agent_runs` should be the only table to disappear, and `llm_requests`
should still be present); the final `downgrade base` / `upgrade head`
pair proves the whole five-migration chain still runs cleanly end to
end.

### 2. Full test suite

```bash
docker compose exec backend pytest -v
```

Expected: every test in `test_runtime_models.py`,
`test_runtime_prompt_builder.py`, `test_runtime_service.py`,
`test_runtime_executor.py`, and `test_runtime_api.py` passes, plus the
P6 additions to `test_migrations.py`, on top of all P1-P5 tests
continuing to pass unmodified (regression). Roughly 218 (P1-P5 baseline)
+ ~65 new P6 tests. No test in this sprint makes a real network call -
every runtime test routes through the `fake` LLM provider, the same way
`test_llm_api.py` does.

### 3. Lint and type checks

```bash
docker compose exec backend ruff check .
docker compose exec backend mypy app
```

Expected: `ruff check` → "All checks passed!"; `mypy app` → "Success: no
issues found in N source files" where N is larger than P5's 64 (new
files: `app/runtime/{__init__,exceptions,models,prompt_builder,service,executor}.py`,
`app/models/agent_run.py`, `app/schemas/runtime.py`, `app/api/v1/runs.py`).

### 4. Manual smoke test (optional but recommended, no API key needed)

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"..."}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# There is no AgentDefinition CRUD API - seed one directly:
docker compose exec db psql -U packverse -d packverse -c \
  "INSERT INTO agent_definitions (id, name, role, version, status, configuration_json, created_at, updated_at) VALUES (gen_random_uuid(), 'smoke-test-agent', 'Tester', 'v1.0', 'active', '{\"system_prompt\": \"You are helpful.\", \"model\": \"fake-v1\"}', now(), now()) RETURNING id;"
# copy the returned id into AGENT_ID below

curl -s -X POST http://localhost:8000/api/v1/runs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"agent_id":"'"$AGENT_ID"'","user_input":"hello"}'

curl -s http://localhost:8000/api/v1/runs -H "Authorization: Bearer $TOKEN"
```

Expected: the `POST /runs` call returns `201` with `"status":"completed"`
and an `output_text` field (the fake provider's deterministic echo);
`GET /runs` lists it.

### If it fails

- **`alembic downgrade -1` from head errors on dropping the FK
  constraint**: check that the constraint name matches exactly what the
  upgrade created (`fk_agent_runs_created_by_user_id_users` /
  `fk_agent_runs_llm_request_id_llm_requests` /
  `fk_agent_runs_agent_id_agent_definitions`) - PostgreSQL is
  case-sensitive here.
- **`test_runtime_executor.py` failures around token/cost fields**:
  confirm the run actually reached `COMPLETED` (check `error_code` in
  the failure output first) - a routing/config issue upstream in the
  LLM Gateway settings would surface here as a `FAILED` run rather than
  a missing-field error.
- **`test_runtime_api.py` failures around dependency overrides**: same
  shape as `test_llm_api.py` - confirm
  `app.dependency_overrides[get_settings]`/`[get_llm_gateway]` are being
  cleared between tests via the `client` fixture.
- **`mypy` failures in `app/runtime/executor.py` around
  `created_by_user_id`**: this field is nullable on `AgentRun` (mirrors
  `llm_requests.user_id`'s `ON DELETE SET NULL`); the executor narrows
  it to a local `owner_id` variable before use specifically so mypy can
  prove it's non-`None` at the `generate_and_persist` call site - if a
  future edit removes that narrowing, expect a mypy failure there, not
  a bug in `llm_service.py`.

## Acceptance (P6)

**Unverified pending a real local run.** Once every command in Part F
above has been run against a real PostgreSQL instance and produced the
expected output, update this section (and the README's Roadmap/status
blockquote) to reflect the confirmed pytest/ruff/mypy results and commit
hash, the same way every prior part was updated after its first real
run. Per explicit CTO instruction, Sprint P7 (Workflow Orchestration)
was implemented immediately after P6 without waiting for this
verification - see Part G below - and Sprint P8 (Asynchronous Job
Execution) was implemented immediately after P7, also without waiting -
see Part H below. Do not start Sprint P9 until P6, P7, and P8 are all
explicitly approved.

## Part G — Sprint P7 (Workflow Orchestration)

**Not yet verified.** Written and statically validated
(`python -m py_compile` across `app/` and `tests/`, no runtime
execution) in the same no-Docker, no-network sandbox as every prior
sprint, immediately after P6 per explicit CTO instruction to proceed
without waiting for P6's own verification. Needs a real local run before
it can be marked verified - same discipline as every prior part: don't
accept a claimed pass without actual pasted command output,
cross-checked numerically against a baseline before trusting a delta.
Because P6 is also still unverified, this run should apply Part F's
steps first (or simply run `alembic upgrade head`, which chains through
both migrations) before proceeding below.

Same reset discipline as prior parts applies: run `docker compose down`
then `docker compose up --build -d` first, so the container picks up
the new `app/workflows/` package and `workflow_runs`/`workflow_step_runs`
migration cleanly. No new third-party dependencies were added this
sprint.

### 1. Migration

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current              # -> d4e6b9a3f1c7 (head)
docker compose exec backend alembic downgrade -1          # drops workflow_runs + workflow_step_runs only
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade a1c8f7d2b3e9   # back to P6 head
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade 7c19e4b8a2d6   # back to P5 head
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade base
docker compose exec backend alembic upgrade head
```

Expected: `d4e6b9a3f1c7 (head)` after the first upgrade; the `downgrade
-1` step removes only `workflow_runs` and `workflow_step_runs` while
every P2-P6 table and column stays intact (spot-check with
`docker compose exec db psql -U packverse -d packverse -c '\dt'` -
`workflow_runs`/`workflow_step_runs` should be the only tables to
disappear, and `agent_runs`/`llm_requests` should still be present); the
final `downgrade base` / `upgrade head` pair proves the whole six-
migration chain still runs cleanly end to end.

### 2. Full test suite

```bash
docker compose exec backend pytest -v
```

Expected: every test in `test_workflow_models.py`,
`test_workflow_definition.py`, `test_workflow_input_builder.py`,
`test_workflow_service.py`, `test_workflow_executor.py`, and
`test_workflow_run_api.py` passes, plus the P7 additions to
`test_migrations.py`, on top of all P1-P6 tests continuing to pass
unmodified (regression). Roughly 218 (P1-P5 baseline) + ~65 P6 tests +
~102 new P7 tests. No test in this sprint makes a real network call -
every workflow executor/API test routes through the `fake` LLM provider,
same as P6's own tests.

### 3. Lint and type checks

```bash
docker compose exec backend ruff check .
docker compose exec backend mypy app
```

Expected: `ruff check` → "All checks passed!"; `mypy app` → "Success: no
issues found in N source files" where N is larger than P6's count (new
files: `app/workflows/{__init__,exceptions,models,definition,input_builder,service,executor}.py`,
`app/models/workflow_run.py`, `app/models/workflow_step_run.py`,
`app/schemas/workflow_run.py`, `app/api/v1/workflow_runs.py`).

### 4. Manual smoke test (optional but recommended, no API key needed)

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"..."}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# There is no AgentDefinition or WorkflowDefinition CRUD API - seed both directly:
docker compose exec db psql -U packverse -d packverse -c \
  "INSERT INTO agent_definitions (id, name, role, version, status, configuration_json, created_at, updated_at) VALUES (gen_random_uuid(), 'smoke-test-agent', 'Tester', 'v1.0', 'active', '{\"system_prompt\": \"You are helpful.\", \"model\": \"fake-v1\"}', now(), now()) RETURNING id;"
# copy the returned id into AGENT_ID below

docker compose exec db psql -U packverse -d packverse -c \
  "INSERT INTO workflow_definitions (id, name, version, status, definition_json, created_at, updated_at) VALUES (gen_random_uuid(), 'smoke-test-workflow', 'v1.0', 'active', '{\"steps\": [{\"step_id\": \"only\", \"name\": \"Only\", \"agent_definition_id\": \"$AGENT_ID\", \"order\": 1}]}', now(), now()) RETURNING id;"
# copy the returned id into WORKFLOW_ID below

curl -s -X POST http://localhost:8000/api/v1/workflow-runs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"workflow_id":"'"$WORKFLOW_ID"'","user_input":"hello"}'

curl -s http://localhost:8000/api/v1/workflow-runs -H "Authorization: Bearer $TOKEN"
```

Expected: the `POST /workflow-runs` call returns `201` with
`"status":"completed"` and an `output_text` field (the fake provider's
deterministic echo); `GET /workflow-runs` lists it; `GET
/workflow-runs/{id}/steps` shows one `completed` step with a matching
`output_text` and a non-null `agent_run_id`.

### If it fails

- **`alembic downgrade -1` from head errors on dropping the FK
  constraint**: check that the constraint name matches exactly what the
  upgrade created (`fk_workflow_step_runs_workflow_run_id_workflow_runs`
  / `fk_workflow_step_runs_agent_id_agent_definitions` /
  `fk_workflow_step_runs_agent_run_id_agent_runs` /
  `fk_workflow_runs_workflow_id_workflow_definitions` /
  `fk_workflow_runs_created_by_user_id_users`) - PostgreSQL is
  case-sensitive here. `workflow_step_runs` must be dropped before
  `workflow_runs` (its FK cascades from the parent).
- **`test_workflow_executor.py` failures around step outputs/timestamps**:
  confirm the run actually reached `COMPLETED` (check the failing step's
  `error_code` first) - a routing/config issue upstream in the LLM
  Gateway settings (same as P6) would surface here as a `FAILED` step
  rather than a missing-field error.
- **`test_workflow_run_api.py` failures around dependency overrides**:
  same shape as `test_runtime_api.py` - confirm
  `app.dependency_overrides[get_settings]`/`[get_llm_gateway]` are being
  cleared between tests via the `client` fixture.
- **`mypy` failures in `app/workflows/executor.py` around `owner_id` or
  `agent_run`**: `owner_id` follows the same nullable-FK narrowing
  pattern as P6's `executor.py` (see that part's note above); `agent_run`
  is narrowed via an explicit `assert agent_run is not None` right after
  the per-step `try`/`except`, since the `except` clause always
  re-raises - if a future edit removes either narrowing, expect a mypy
  failure there, not a bug in `app.runtime`.

## Acceptance (P7)

**Unverified pending a real local run.** Once every command in Part G
above has been run against a real PostgreSQL instance and produced the
expected output, update this section (and the README's Roadmap/status
blockquote) to reflect the confirmed pytest/ruff/mypy results and commit
hash, the same way every prior part was updated after its first real
run. Per explicit CTO instruction, Sprint P8 (Asynchronous Job
Execution) was implemented immediately after P7 without waiting for
this verification - see Part H below. Do not start Sprint P9 until P6,
P7, and P8 are all explicitly approved.

## Part H — Sprint P8 (Asynchronous Job Execution)

**Not yet verified.** Written and statically validated
(`python -m py_compile` across `app/` and `tests/`, plus a manual
unused-import/line-length sweep, no runtime execution) in the same
no-Docker, no-network sandbox as every prior sprint, immediately after
P7 per explicit CTO instruction to proceed without waiting for P7's own
verification. Needs a real local run before it can be marked verified -
same discipline as every prior part. Because P6 and P7 are also still
unverified, this run should apply Parts F and G's steps first (or simply
run `alembic upgrade head`, which chains through all three migrations)
before proceeding below.

This is the first sprint with a genuinely new moving part: a second
long-running process (`worker`), not just new tables/endpoints in the
existing `backend` container. Run `docker compose down` then
`docker compose up --build -d` first, so Compose picks up the new
`worker` service definition and both containers rebuild from the same
updated image. No new third-party dependencies were added this sprint
(the worker's `psycopg2` healthcheck script reuses the `psycopg2-binary`
dependency Alembic already required since P1).

### 1. Migration

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current              # -> b7f3e9a1c5d2 (head)
docker compose exec backend alembic downgrade -1          # drops worker_heartbeats + P8's jobs columns only
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade d4e6b9a3f1c7   # back to P7 head
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade a1c8f7d2b3e9   # back to P6 head
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade 7c19e4b8a2d6   # back to P5 head
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade base
docker compose exec backend alembic upgrade head
```

Expected: `b7f3e9a1c5d2 (head)` after the first upgrade; the `downgrade
-1` step drops only `worker_heartbeats` and the nine columns P8 added to
`jobs` (`target_run_id`, `error_code`, `attempt_count`, `max_attempts`,
`next_attempt_at`, `lease_expires_at`, `heartbeat_at`, `worker_id`,
`cancel_requested_at`) while every P2-P7 table/column (including `jobs`
itself, and `workflow_runs`/`workflow_step_runs`) stays intact
(spot-check with `docker compose exec db psql -U packverse -d packverse
-c '\d jobs'` - only those nine columns should be gone, `id`/`job_type`/
`status`/`input_json`/`output_json`/`error_message`/`started_at`/
`completed_at` should remain); the final `downgrade base` / `upgrade
head` pair proves the whole seven-migration chain still runs cleanly end
to end.

### 2. Full test suite

```bash
docker compose exec backend pytest -v
```

Expected: every test in `test_job_models.py`, `test_job_queue.py`,
`test_job_service.py`, `test_worker_dispatch.py`, `test_worker_runner.py`,
and `test_worker_healthcheck.py` passes, plus the P8 additions to
`test_migrations.py` and `test_health.py`, and the rewritten
`test_runtime_api.py`/`test_workflow_run_api.py` (now asserting `202`/
`queued` from `POST /runs`/`POST /workflow-runs` instead of the old
synchronous `201`/`completed`), on top of all other P1-P7 tests
continuing to pass unmodified (regression). Roughly 218 (P1-P5 baseline)
+ ~65 P6 tests + ~102 P7 tests + ~90 new/changed P8 tests. No test in
this sprint makes a real network call - every job/worker test routes
through the `fake` LLM provider, same as P6/P7's own tests, and the
worker tests use their own `worker_session_factory` fixture (real
commits against the isolated per-test schema) rather than the shared
rollback-based `db_session` fixture, since the worker legitimately opens
many independent sessions over its lifetime.

### 3. Lint and type checks

```bash
docker compose exec backend ruff check .
docker compose exec backend mypy app
```

Expected: `ruff check` → "All checks passed!"; `mypy app` → "Success: no
issues found in N source files" where N is larger than P7's count (new
files: `app/jobs/{__init__,exceptions,models,queue,service}.py`,
`app/worker/{__init__,dispatch,runner,main,__main__,healthcheck}.py`,
`app/models/worker_heartbeat.py`).

### 4. Worker + health checks

```bash
docker compose ps                                    # worker should show "healthy"
docker compose logs worker                            # should show periodic poll/heartbeat log lines
curl -s http://localhost:8000/api/v1/health
```

Expected: `docker compose ps` shows all three services (`db`, `backend`,
`worker`) as healthy; `docker compose logs worker` shows startup log
lines (recovery pass, heartbeat) and no tracebacks; `GET
/api/v1/health` returns
`{"status":"ok","database":"connected","queue":"connected","worker":"available"}`
once the worker has had a few seconds to send its first heartbeat.

### 5. Manual smoke test (optional but recommended, no API key needed)

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"..."}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# There is no AgentDefinition CRUD API - seed one directly:
docker compose exec db psql -U packverse -d packverse -c \
  "INSERT INTO agent_definitions (id, name, role, version, status, configuration_json, created_at, updated_at) VALUES (gen_random_uuid(), 'smoke-test-agent-p8', 'Tester', 'v1.0', 'active', '{\"system_prompt\": \"You are helpful.\", \"model\": \"fake-v1\"}', now(), now()) RETURNING id;"
# copy the returned id into AGENT_ID below

curl -s -X POST http://localhost:8000/api/v1/runs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"agent_id":"'"$AGENT_ID"'","user_input":"hello"}'
# -> 202 Accepted, {"status":"queued", ...} - note: 202, not 201, and queued not completed

RUN_ID=...   # from the response above
sleep 2      # give the worker a moment to claim and execute it
curl -s http://localhost:8000/api/v1/runs/$RUN_ID -H "Authorization: Bearer $TOKEN"
```

Expected: the `POST /runs` call returns `202` with `"status":"queued"`
and `"output_text":null`; a few seconds later, `GET /runs/{id}` shows
`"status":"completed"` and a non-null `output_text` (the fake provider's
deterministic echo) - proving the worker actually claimed and executed
the job, without the API request itself ever calling the LLM Gateway.

### If it fails

- **`docker compose ps` shows `worker` as `unhealthy` or restarting**:
  check `docker compose logs worker` first - a missing/incorrect
  `DATABASE_URL`-equivalent env var (same `POSTGRES_*` vars `backend`
  uses) is the most likely cause, since the worker builds its own engine
  from the same `Settings` the API does.
- **`GET /api/v1/health` shows `"worker":"unavailable"` even though the
  worker container is healthy**: the container-level `HEALTHCHECK` and
  the HTTP-level `/health` field use the same staleness threshold
  (`worker_heartbeat_stale_after_seconds`) but are two separate checks -
  give it a few more seconds after `docker compose up`, or check
  `docker compose logs worker` for whether it's actually reaching its
  heartbeat-upsert code (an early crash before the first heartbeat would
  explain this).
- **A queued run never leaves `queued`**: check `docker compose logs
  worker` for claim log lines - if the worker isn't polling at all,
  confirm the `worker` service's `command` override
  (`python -m app.worker`) actually took effect (`docker compose exec
  worker ps aux` should show that process, not `uvicorn`).
- **`alembic downgrade -1` from head errors dropping a column**: the
  nine P8 columns must be dropped in the exact reverse order the
  migration's `downgrade()` lists them (see
  `b7f3e9a1c5d2_add_job_queue_fields_and_worker_heartbeats.py`) - this
  should already be correct as committed, but double-check if a manual
  edit ever touches that file.
- **`test_worker_runner.py` timing-sensitive tests are flaky**: the
  lease-renewal and stale-job-recovery tests use short real `asyncio.sleep`
  windows (order of 100-200ms) tuned for a healthy local Postgres
  connection - a very slow/loaded CI runner could need those intervals
  widened; this is a test-tuning issue, not a correctness bug in
  `app/worker/runner.py` itself.
- **`mypy` failures in `app/jobs/exceptions.py` around the `JobStatus`
  forward reference**: this mirrors `app/runtime/exceptions.py`'s and
  `app/workflows/exceptions.py`'s existing `TYPE_CHECKING`-only import
  pattern exactly (avoids a runtime circular import between
  `app.jobs.exceptions` and `app.models.enums`) - if mypy complains here,
  the same fix already applied to those two files' `InvalidRunTransitionError`
  /`InvalidWorkflowRunTransitionError` applies here too.

## Acceptance (P8)

**Unverified pending a real local run.** Once every command in Part H
above has been run against a real PostgreSQL instance and produced the
expected output, update this section (and the README's Roadmap/status
blockquote) to reflect the confirmed pytest/ruff/mypy results and commit
hash, the same way every prior part was updated after its first real
run. Per explicit CTO instruction: do not start Sprint P9 until P6, P7,
and P8 are all explicitly approved.
