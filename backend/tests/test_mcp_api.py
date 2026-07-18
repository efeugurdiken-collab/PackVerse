"""Tests for the MCP client API (Sprint P9B): /api/v1/mcp/*.

GET /mcp/servers reads directly off Settings (no network call).
GET /mcp/servers/{name}/tools makes a real MCPClient call whose HTTP
requests are intercepted by pytest-httpx's `httpx_mock` fixture - no
real MCP server, no real network access.
"""
from __future__ import annotations

import json

import pytest

from app.core.config import Settings, get_settings
from app.main import app
from app.models.enums import UserRole

BASE = "/api/v1/mcp"
MCP_URL = "https://mcp.test/mcp"


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {"jwt_secret_key": "x" * 32}
    defaults.update(overrides)
    return Settings(**defaults)


def _settings_with_server(**overrides: object) -> Settings:
    servers = json.dumps([{"name": "test-server", "base_url": MCP_URL}])
    return _settings(mcp_servers_json=servers, **overrides)


def _override(settings: Settings) -> None:
    app.dependency_overrides[get_settings] = lambda: settings


@pytest.fixture
async def viewer_headers(make_user, auth_headers) -> dict[str, str]:
    user = await make_user(role=UserRole.VIEWER)
    return auth_headers(user)


def _queue_handshake(httpx_mock) -> None:
    httpx_mock.add_response(
        url=MCP_URL,
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}},
    )
    httpx_mock.add_response(url=MCP_URL, method="POST", status_code=202, json={})


# --- GET /mcp/servers ------------------------------------------------


async def test_list_servers_requires_authentication(client) -> None:
    _override(_settings_with_server())
    response = await client.get(f"{BASE}/servers")
    assert response.status_code == 401


async def test_viewer_can_list_servers(client, viewer_headers) -> None:
    _override(_settings_with_server())
    response = await client.get(f"{BASE}/servers", headers=viewer_headers)
    assert response.status_code == 200
    assert response.json() == [{"name": "test-server", "base_url": MCP_URL}]


async def test_no_servers_configured_returns_empty_list(client, viewer_headers) -> None:
    _override(_settings())
    response = await client.get(f"{BASE}/servers", headers=viewer_headers)
    assert response.status_code == 200
    assert response.json() == []


# --- GET /mcp/servers/{name}/tools ------------------------------------


async def test_list_tools_requires_authentication(client) -> None:
    _override(_settings_with_server())
    response = await client.get(f"{BASE}/servers/test-server/tools")
    assert response.status_code == 401


async def test_viewer_can_list_tools(client, viewer_headers, httpx_mock) -> None:
    _override(_settings_with_server())
    _queue_handshake(httpx_mock)
    httpx_mock.add_response(
        url=MCP_URL,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {
                        "name": "get_weather",
                        "description": "Look up the weather",
                        "inputSchema": {"type": "object"},
                    }
                ]
            },
        },
    )

    response = await client.get(f"{BASE}/servers/test-server/tools", headers=viewer_headers)

    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "get_weather",
            "description": "Look up the weather",
            "input_schema": {"type": "object"},
        }
    ]


async def test_list_tools_for_unknown_server_returns_404(client, viewer_headers) -> None:
    _override(_settings_with_server())
    response = await client.get(f"{BASE}/servers/no-such-server/tools", headers=viewer_headers)
    assert response.status_code == 404


async def test_list_tools_for_unreachable_server_returns_502(
    client, viewer_headers, httpx_mock
) -> None:
    _override(_settings_with_server())
    httpx_mock.add_response(url=MCP_URL, method="POST", status_code=500)

    response = await client.get(f"{BASE}/servers/test-server/tools", headers=viewer_headers)

    assert response.status_code == 502
