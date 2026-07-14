# syntax=docker/dockerfile:1

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is needed for the container HEALTHCHECK below. psycopg2-binary
# (declared in pyproject.toml, used by Alembic's sync driver) ships
# prebuilt wheels, so no build toolchain is required for it specifically.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY tests ./tests

# Installed with the [dev] extra (pytest, ruff, mypy, httpx) so this same
# image can run Sprint P1/P2 runtime verification in place, not just serve
# the app. Revisit with a multi-stage build before this ships to prod.
RUN pip install --no-cache-dir ".[dev]"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
