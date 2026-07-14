"""Asset API schemas.

Only AssetRead exists in Sprint P2 - assets are created internally by the
future Storage sprint's upload pipeline, not via a client-facing Create
endpoint, so there is nothing to validate on the way in yet.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    asset_type: str
    filename: str
    storage_key: str
    mime_type: str
    size_bytes: int
    checksum: str
    created_at: datetime
    updated_at: datetime
