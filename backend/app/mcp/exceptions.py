"""MCP client exception hierarchy (Sprint P9B).

app/mcp/client.py raises these; app/api/v1/mcp.py is the only place that
maps them to HTTP status codes - the same "domain error, translated at
the API boundary" pattern app/llm/exceptions.py already established for
the LLM Gateway.

No retry policy or `retryable` flag here (unlike LLMError) - retrying a
failed MCP call is a runtime-loop concern, out of scope until this
client is wired into app/runtime/executor.py in a later sprint.
"""
from __future__ import annotations


class MCPError(Exception):
    """Base class for all MCP client errors. Messages are deliberately
    generic - never include an auth token; see app/mcp/client.py's
    _headers for where the token is kept out of every exception path."""


class MCPServerNotConfigured(MCPError):
    """Raised when a caller asks for a server name that isn't in
    MCP_SERVERS_JSON - see app/mcp/factory.py."""

    def __init__(self, server_name: str) -> None:
        super().__init__(f"MCP server {server_name!r} is not configured")
        self.server_name = server_name


class MCPConnectionError(MCPError):
    """The server could not be reached at all (connection refused, DNS
    failure, TLS error, ...)."""

    def __init__(self, server_name: str, reason: str = "") -> None:
        message = f"MCP server {server_name!r} is unreachable"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)
        self.server_name = server_name


class MCPTimeoutError(MCPError):
    def __init__(self, server_name: str) -> None:
        super().__init__(f"Request to MCP server {server_name!r} timed out")
        self.server_name = server_name


class MCPProtocolError(MCPError):
    """The server responded, but not in a way this client understands -
    a non-200/202 HTTP status, a non-JSON body, a JSON-RPC response
    missing both `result` and `error`, or a malformed tool/result
    shape."""

    def __init__(self, server_name: str, detail: str = "") -> None:
        message = f"MCP server {server_name!r} returned an unexpected response"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.server_name = server_name


class MCPToolCallError(MCPError):
    """The server accepted the tools/call request but returned a
    JSON-RPC error for it (unknown tool, invalid arguments, tool
    execution failure, ...) - kept distinct from MCPProtocolError since
    this is a legitimate, expected outcome of calling a tool, not a
    client/server contract violation."""

    def __init__(self, server_name: str, tool_name: str, detail: str = "") -> None:
        message = f"MCP server {server_name!r} rejected tool call {tool_name!r}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.server_name = server_name
        self.tool_name = tool_name
