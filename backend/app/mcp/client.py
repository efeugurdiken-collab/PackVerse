"""MCP client (Sprint P9B): Streamable HTTP transport only.

Hand-rolled over httpx rather than the official `mcp` SDK - the same
choice already made for app/llm/providers/anthropic.py and
openai_compatible.py: one HTTP dependency, thin enough to unit test by
mocking httpx responses instead of a heavier SDK client.

Stateless per call: list_tools() and call_tool() each perform their own
initialize -> notifications/initialized -> real-call sequence over a
fresh connection, then close it. No persistent MCP session is held
across calls - nothing in this sprint calls this client in a hot loop
that would need one; see app/mcp/client.py's class docstring if that
changes.

Scope: tools only (no resources/prompts), and only servers that answer
the Streamable HTTP transport with a single JSON object per request (not
an SSE stream) are supported this sprint.
"""
from __future__ import annotations

import httpx

from app.mcp.exceptions import (
    MCPConnectionError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPToolCallError,
)
from app.mcp.models import MCPTool, MCPToolResult

_PROTOCOL_VERSION = "2025-06-18"
_CLIENT_INFO = {"name": "packverse-platform", "version": "0.1.0"}


class MCPClient:
    """One client per configured MCP server - see app/mcp/factory.py for
    how `server_name`/`base_url`/`auth_token` are resolved from
    Settings.mcp_servers_list."""

    def __init__(
        self,
        *,
        server_name: str,
        base_url: str,
        auth_token: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._server_name = server_name
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        # The bearer token never appears in any exception message this
        # client raises - every app.mcp.exceptions.MCPError subclass
        # takes a server name, not a credential.
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers

    async def _post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
        *,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        try:
            response = await client.post(self._base_url, headers=self._headers(), json=payload)
        except httpx.TimeoutException as exc:
            raise MCPTimeoutError(self._server_name) from exc
        except httpx.RequestError as exc:
            raise MCPConnectionError(self._server_name, str(exc)) from exc

        if response.status_code not in expected_statuses:
            raise MCPProtocolError(self._server_name, f"HTTP {response.status_code}")
        return response

    async def _initialize(self, client: httpx.AsyncClient) -> None:
        """Performs the MCP handshake: `initialize`, then the
        `notifications/initialized` notification. A JSON-RPC
        notification gets no `result`/`error` body back per the MCP
        spec - a real Streamable HTTP server typically answers it with
        202 Accepted and an empty body, so this only checks the HTTP
        status, never parses a response body for it."""
        response = await self._post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                },
            },
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise MCPProtocolError(
                self._server_name, "initialize: response body was not valid JSON"
            ) from exc
        if "error" in body:
            raise MCPProtocolError(self._server_name, f"initialize failed: {body['error']}")
        if "result" not in body:
            raise MCPProtocolError(self._server_name, "initialize: no result in response")

        await self._post(
            client,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expected_statuses=(200, 202),
        )

    async def list_tools(self) -> tuple[MCPTool, ...]:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            await self._initialize(client)
            response = await self._post(
                client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            )
            try:
                body = response.json()
            except ValueError as exc:
                raise MCPProtocolError(
                    self._server_name, "tools/list: response body was not valid JSON"
                ) from exc
            if "error" in body:
                raise MCPProtocolError(self._server_name, f"tools/list failed: {body['error']}")

            try:
                raw_tools = body["result"]["tools"]
                tools = tuple(
                    MCPTool(
                        name=str(t["name"]),
                        description=str(t.get("description", "")),
                        input_schema=dict(t.get("inputSchema") or {}),
                    )
                    for t in raw_tools
                )
            except (KeyError, TypeError) as exc:
                raise MCPProtocolError(
                    self._server_name, f"malformed tools/list response: {exc}"
                ) from exc
            return tools

    async def call_tool(self, name: str, arguments: dict[str, object]) -> MCPToolResult:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            await self._initialize(client)
            response = await self._post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            try:
                body = response.json()
            except ValueError as exc:
                raise MCPProtocolError(
                    self._server_name, "tools/call: response body was not valid JSON"
                ) from exc

            if "error" in body:
                raise MCPToolCallError(self._server_name, name, str(body["error"]))

            try:
                result = body["result"]
                content_blocks = result["content"]
                if not isinstance(content_blocks, list):
                    raise TypeError("content must be a list")
                text_blocks = [
                    str(block["text"])
                    for block in content_blocks
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                is_error = bool(result.get("isError", False))
            except (KeyError, TypeError) as exc:
                raise MCPProtocolError(
                    self._server_name, f"malformed tools/call response: {exc}"
                ) from exc

            return MCPToolResult(content="".join(text_blocks), is_error=is_error)
