"""WorkflowDefinition API schemas.

Read-only in Sprint P2: definitions are seeded from the PackVerse OS
vault's 06 Workflows/ specifications, not created via the API yet.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import WorkflowStatus


class WorkflowDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    version: str
    status: WorkflowStatus
    definition_json: dict[str, object]
    created_at: datetime
    updated_at: datetime
