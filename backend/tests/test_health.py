"""Tests for the /health endpoint.

Note: these run against whatever PostgreSQL instance is configured via
environment variables. In CI/local dev, run via docker compose so the
db service is available - see README.md.

pytest-asyncio is configured with asyncio_mode = "auto" in pyproject.toml,
so async test functions are picked up automatically without markers.
"""


async def test_root_endpoint(client) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"


async def test_health_endpoint_returns_200(client) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["database"] in {"connected", "unreachable"}
