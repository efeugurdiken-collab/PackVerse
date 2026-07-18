"""MCP client API schemas (Sprint P9B).

Pydantic v2 response models for /api/v1/mcp/* - the HTTP-facing
counterpart to the framework-agnostic dataclasses in app/mcp/models.py.
Read-only this sprint: no request body schemas, since both endpoints are
GET (server/tool discovery only - no CRUD, the same "configured, not
managed via API" posture as AgentDefinition/WorkflowDefinition).
"""
from __future__ import annotations

from pydantic import BaseModel


class MCPServerInfo(BaseModel):
    name: str
    base_url: str


class MCPToolInfo(BaseModel):
    name: str
    description: str
    input_schema: dict[str, object]
