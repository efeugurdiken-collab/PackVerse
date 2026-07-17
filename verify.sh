#!/usr/bin/env bash
# Local verification runner for packverse-platform.
#
# Runs the exact same commands the CTO has been running by hand after
# every sprint (docker compose ps, alembic current, pytest, ruff, mypy,
# git status) in sequence, from the repository root. Stops at the first
# failing step (set -euo pipefail + no `|| true` anywhere) so a red step
# is never masked by a later green one.
#
# Usage:
#   ./verify.sh
#
# Requires: the stack already brought up via `docker compose up --build -d`
# (this script does not start it for you - see README.md Setup), and
# `curl` available on the host (Sprint P8's health-endpoint checks below
# run against the host-published port, not inside a container).

set -euo pipefail

step() {
    echo ""
    echo "=================================================================="
    echo "==> $1"
    echo "=================================================================="
}

step "docker compose ps"
docker compose ps

step "alembic current"
docker compose exec backend alembic current

step "pytest -v"
docker compose exec backend pytest -v

step "ruff check ."
docker compose exec backend ruff check .

step "mypy app"
docker compose exec backend mypy app

# --- Sprint P8: worker + queue verification ---
# `docker compose ps` above already shows all three services (db,
# backend, worker) and their state, but the checks below go further:
# they specifically assert the worker container's own Docker
# HEALTHCHECK (app/worker/healthcheck.py) has passed, and that the API's
# /health endpoint independently agrees the queue is reachable and a
# worker heartbeat is fresh - the same two signals a real operator would
# check before trusting the async job pipeline is actually working.

step "worker container health (docker inspect)"
WORKER_CID="$(docker compose ps -q worker)"
if [ -z "$WORKER_CID" ]; then
    echo "no 'worker' container found - is the worker service defined and started?"
    exit 1
fi
WORKER_HEALTH="$(docker inspect --format='{{.State.Health.Status}}' "$WORKER_CID")"
echo "worker container health: $WORKER_HEALTH"
if [ "$WORKER_HEALTH" != "healthy" ]; then
    echo "worker container is not healthy (status: $WORKER_HEALTH)"
    echo "check its logs with: docker compose logs worker"
    exit 1
fi

step "docker compose logs worker (tail, for visibility)"
docker compose logs --tail=30 worker

step "GET /api/v1/health (database + queue + worker availability)"
HEALTH_JSON="$(curl -sf http://localhost:8000/api/v1/health)"
echo "$HEALTH_JSON"
echo "$HEALTH_JSON" | grep -q '"database":"connected"' \
    || { echo "database not connected"; exit 1; }
echo "$HEALTH_JSON" | grep -q '"queue":"connected"' \
    || { echo "queue not connected"; exit 1; }
echo "$HEALTH_JSON" | grep -q '"worker":"available"' \
    || { echo "worker not reported available - has the worker container sent a heartbeat yet?"; exit 1; }

step "git status --short"
git status --short

echo ""
echo "=================================================================="
echo "ALL CHECKS PASSED"
echo "=================================================================="
