"""Builds MCPClient instances from the configured MCP_SERVERS_JSON
registry (Sprint P9B).

Mirrors app/llm/factory.py's role for the LLM Gateway: business logic
(app/api/v1/mcp.py) depends only on app.mcp.client.MCPClient, and this
module is the only place server configuration is turned into a client
instance. Deliberately not @lru_cache-d, unlike
app.llm.factory.get_provider_registry - Settings.mcp_servers_list is
tiny, uncached config data (same rationale as
Settings.llm_model_aliases_map/llm_pricing_map), and constructing an
MCPClient does no I/O, so there is nothing expensive here worth caching.
"""
from __future__ import annotations

from app.core.config import Settings
from app.mcp.client import MCPClient
from app.mcp.exceptions import MCPServerNotConfigured
from app.mcp.models import MCPServerConfig


def _server_registry(settings: Settings) -> dict[str, MCPServerConfig]:
    return {
        str(entry["name"]): MCPServerConfig(
            name=str(entry["name"]),
            base_url=str(entry["base_url"]),
            auth_token=(str(entry["auth_token"]) if entry.get("auth_token") else None),
        )
        for entry in settings.mcp_servers_list
    }


def list_configured_servers(settings: Settings) -> list[MCPServerConfig]:
    """Read-only listing for GET /api/v1/mcp/servers - see
    app/api/v1/mcp.py."""
    return list(_server_registry(settings).values())


def build_mcp_client(server_name: str, settings: Settings) -> MCPClient:
    """Raises MCPServerNotConfigured if server_name isn't in
    MCP_SERVERS_JSON - app/api/v1/mcp.py maps that to a 404, the same
    "unknown id" shape as the rest of this app's read endpoints."""
    config = _server_registry(settings).get(server_name)
    if config is None:
        raise MCPServerNotConfigured(server_name)
    return MCPClient(
        server_name=config.name,
        base_url=config.base_url,
        auth_token=config.auth_token,
        timeout_seconds=settings.mcp_timeout_seconds,
    )
