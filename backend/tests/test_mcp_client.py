"""Tests for app.mcp.client.MCPClient (Sprint P9B).

Uses pytest-httpx's `httpx_mock` fixture to intercept the client's
httpx.AsyncClient calls - no real network access, no real MCP server
required. Every list_tools()/call_tool() performs its own
initialize -> notifications/initialized -> real-call sequence (three
POSTs to the same URL) per app/mcp/client.py's stateless-per-call
design - tests that reach the real call queue three mocked responses in
that order via _queue_handshake below.
"""
from __future__ import annotations

import json as _json

import httpx
import pytest

from app.mcp.client import MCPClient
from app.mcp.exceptions import (
    MCPConnectionError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPToolCallError,
)

BASE_URL = "https://mcp.test/mcp"


def _client(**overrides: object) -> MCPClient:
    defaults: dict[str, object] = {
        "server_name": "test-server",
        "base_url": BASE_URL,
        "timeout_seconds": 5.0,
    }
    defaults.update(overrides)
    return MCPClient(**defaults)  # type: ignore[arg-type]


def _initialize_body() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "serverInfo": {"name": "test-mcp-server", "version": "0.1.0"},
        },
    }


def _queue_handshake(httpx_mock) -> None:
    httpx_mock.add_response(url=BASE_URL, method="POST", json=_initialize_body())
    httpx_mock.add_response(url=BASE_URL, method="POST", status_code=202, json={})


# --- list_tools ------------------------------------------------------


async def test_list_tools_returns_parsed_tools(httpx_mock) -> None:
    _queue_handshake(httpx_mock)
    httpx_mock.add_response(
        url=BASE_URL,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {
                        "name": "get_weather",
                        "description": "Look up the current weather for a city",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                        },
                    }
                ]
            },
        },
    )

    tools = await _client().list_tools()

    assert len(tools) == 1
    assert tools[0].name == "get_weather"
    assert tools[0].description == "Look up the current weather for a city"
    assert tools[0].input_schema == {
        "type": "object",
        "properties": {"city": {"type": "string"}},
    }


async def test_list_tools_sends_initialize_before_listing(httpx_mock) -> None:
    _queue_handshake(httpx_mock)
    httpx_mock.add_response(
        url=BASE_URL, method="POST", json={"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
    )

    await _client().list_tools()

    requests = httpx_mock.get_requests()
    assert len(requests) == 3
    assert _json.loads(requests[0].content)["method"] == "initialize"
    assert _json.loads(requests[1].content)["method"] == "notifications/initialized"
    assert _json.loads(requests[2].content)["method"] == "tools/list"


async def test_list_tools_with_no_tools_returns_empty_tuple(httpx_mock) -> None:
    _queue_handshake(httpx_mock)
    httpx_mock.add_response(
        url=BASE_URL, method="POST", json={"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
    )

    tools = await _client().list_tools()

    assert tools == ()


async def test_list_tools_maps_malformed_tool_entry_to_protocol_error(httpx_mock) -> None:
    _queue_handshake(httpx_mock)
    httpx_mock.add_response(
        url=BASE_URL,
        method="POST",
        json={"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"description": "missing name"}]}},
    )

    with pytest.raises(MCPProtocolError):
        await _client().list_tools()


async def test_list_tools_maps_initialize_error_to_protocol_error(httpx_mock) -> None:
    httpx_mock.add_response(
        url=BASE_URL,
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad request"}},
    )

    with pytest.raises(MCPProtocolError):
        await _client().list_tools()


# --- call_tool ---------------------------------------------------------


async def test_call_tool_returns_result(httpx_mock) -> None:
    _queue_handshake(httpx_mock)
    httpx_mock.add_response(
        url=BASE_URL,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": "sunny, 72F"}], "isError": False},
        },
    )

    result = await _client().call_tool("get_weather", {"city": "nyc"})

    assert result.content == "sunny, 72F"
    assert result.is_error is False


async def test_call_tool_concatenates_multiple_text_blocks(httpx_mock) -> None:
    _queue_handshake(httpx_mock)
    httpx_mock.add_response(
        url=BASE_URL,
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [{"type": "text", "text": "part1"}, {"type": "text", "text": "part2"}],
                "isError": False,
            },
        },
    )

    result = await _client().call_tool("get_weather", {"city": "nyc"})

    assert result.content == "part1part2"


async def test_call_tool_server_error_raises_tool_call_error(httpx_mock) -> None:
    _queue_handshake(httpx_mock)
    httpx_mock.add_response(
        url=BASE_URL,
        method="POST",
        json={"jsonrpc": "2.0", "id": 2, "error": {"code": -32602, "message": "unknown tool"}},
    )

    with pytest.raises(MCPToolCallError) as excinfo:
        await _client().call_tool("nonexistent_tool", {})
    assert excinfo.value.tool_name == "nonexistent_tool"


async def test_call_tool_malformed_result_raises_protocol_error(httpx_mock) -> None:
    _queue_handshake(httpx_mock)
    httpx_mock.add_response(
        url=BASE_URL,
        method="POST",
        json={"jsonrpc": "2.0", "id": 2, "result": {"content": "not-a-list"}},
    )

    with pytest.raises(MCPProtocolError):
        await _client().call_tool("get_weather", {"city": "nyc"})


# --- Transport-level errors ---------------------------------------------


async def test_timeout_mapping(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.TimeoutException("timed out"))

    with pytest.raises(MCPTimeoutError):
        await _client().list_tools()


async def test_connection_error_mapping(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))

    with pytest.raises(MCPConnectionError):
        await _client().list_tools()


async def test_non_200_status_raises_protocol_error(httpx_mock) -> None:
    httpx_mock.add_response(url=BASE_URL, method="POST", status_code=500)

    with pytest.raises(MCPProtocolError):
        await _client().list_tools()


async def test_non_json_body_raises_protocol_error(httpx_mock) -> None:
    httpx_mock.add_response(url=BASE_URL, method="POST", content=b"not json")

    with pytest.raises(MCPProtocolError):
        await _client().list_tools()


# --- Auth token handling -------------------------------------------------


async def test_auth_token_sent_as_bearer_header(httpx_mock) -> None:
    _queue_handshake(httpx_mock)
    httpx_mock.add_response(
        url=BASE_URL, method="POST", json={"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
    )

    await _client(auth_token="test-mcp-token").list_tools()

    sent = httpx_mock.get_requests()[0]
    assert sent.headers["authorization"] == "Bearer test-mcp-token"


async def test_auth_token_never_appears_in_exception_message(httpx_mock) -> None:
    httpx_mock.add_response(url=BASE_URL, method="POST", status_code=500)

    with pytest.raises(MCPProtocolError) as excinfo:
        await _client(auth_token="super-secret-token").list_tools()
    assert "super-secret-token" not in str(excinfo.value)
