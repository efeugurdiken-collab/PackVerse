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
# (this script does not start it for you - see README.md Setup).

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

step "git status --short"
git status --short

echo ""
echo "=================================================================="
echo "ALL CHECKS PASSED"
echo "=================================================================="
