"""AgentDefinition API schemas.

Read-only in Sprint P2: definitions are seeded from the PackVerse OS
vault's 05 Agents/ specifications, not created via the API yet.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import AgentStatus


class AgentDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    role: str
    version: str
    status: AgentStatus
    configuration_json: dict[str, object]
    created_at: datetime
    updated_at: datetime
