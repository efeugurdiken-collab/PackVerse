"""Internal, framework-agnostic MCP client data structures (Sprint P9B).

Plain dataclasses, not Pydantic models: app/mcp/ must not import FastAPI
or SQLAlchemy, mirroring app/llm/models.py's split for the LLM Gateway.
app/schemas/mcp.py holds the separate, Pydantic-based API response
schemas FastAPI serializes; app/api/v1/mcp.py converts between the two
at the boundary.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MCPServerConfig:
    """One entry from the MCP_SERVERS_JSON setting, resolved by
    app/mcp/factory.py - see app/core/config.py's mcp_servers_list
    property for the raw parsed form."""

    name: str
    base_url: str
    auth_token: str | None = None


@dataclass(frozen=True)
class MCPTool:
    """A single tool an MCP server exposes, normalized from its
    `tools/list` response (which uses `inputSchema`, camelCase, on the
    wire - app/mcp/client.py maps it to this snake_case field)."""

    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True)
class MCPToolResult:
    """Result of a single `tools/call`. `content` concatenates every
    text content block in the response - the same "concatenate text
    blocks" simplification app/llm/providers/anthropic.py already makes
    for its own content-block response shape. Non-text content blocks
    (images, embedded resources, ...) are out of scope this sprint."""

    content: str
    is_error: bool
