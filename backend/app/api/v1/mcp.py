"""MCP client API endpoints (Sprint P9B).

Read-only: lists configured MCP servers and, for a given server, the
tools it currently exposes. Same viewer-can-read access matrix as the
LLM Gateway's providers/models/health endpoints (app/api/v1/llm.py) -
there is no write path here (servers are configured via
MCP_SERVERS_JSON, not an API), so there is no "who can generate" analog
to gate.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_roles
from app.core.config import Settings, get_settings
from app.mcp.exceptions import (
    MCPConnectionError,
    MCPError,
    MCPProtocolError,
    MCPServerNotConfigured,
    MCPTimeoutError,
    MCPToolCallError,
)
from app.mcp.factory import build_mcp_client, list_configured_servers
from app.models.enums import UserRole
from app.schemas.mcp import MCPServerInfo, MCPToolInfo

router = APIRouter(prefix="/mcp", tags=["mcp"])

_can_read = require_roles(UserRole.VIEWER, UserRole.OPERATOR, UserRole.ADMIN)


def _map_mcp_error(exc: MCPError) -> HTTPException:
    """The one place an app.mcp.exceptions.MCPError becomes an HTTP
    status code - never a raw httpx exception or JSON-RPC error body,
    the same "map at the API boundary" discipline as
    app/api/v1/llm.py's _map_llm_error."""
    if isinstance(exc, MCPServerNotConfigured):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, MCPTimeoutError):
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    if isinstance(exc, MCPConnectionError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(exc, MCPToolCallError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(exc, MCPProtocolError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return HTTPException(  # pragma: no cover
        status_code=status.HTTP_502_BAD_GATEWAY, detail="MCP server error"
    )


@router.get("/servers", response_model=list[MCPServerInfo], dependencies=[Depends(_can_read)])
async def list_servers(settings: Settings = Depends(get_settings)) -> list[MCPServerInfo]:
    return [
        MCPServerInfo(name=s.name, base_url=s.base_url)
        for s in list_configured_servers(settings)
    ]


@router.get(
    "/servers/{server_name}/tools",
    response_model=list[MCPToolInfo],
    dependencies=[Depends(_can_read)],
)
async def list_tools(
    server_name: str, settings: Settings = Depends(get_settings)
) -> list[MCPToolInfo]:
    try:
        client = build_mcp_client(server_name, settings)
        tools = await client.list_tools()
    except MCPError as exc:
        raise _map_mcp_error(exc) from exc
    return [
        MCPToolInfo(name=t.name, description=t.description, input_schema=t.input_schema)
        for t in tools
    ]
