"""Asset API schemas.

There is no AssetCreate schema: uploads arrive as multipart/form-data
(see app/api/v1/assets.py's use of FastAPI's UploadFile/Form, not a JSON
body), so there is nothing to validate via a Pydantic model on the way
in beyond what app/services/asset_service.py already checks against the
raw bytes and headers.

storage_key is deliberately never exposed here - see the Security rule
against exposing internal storage keys outside an admin-only schema;
this sprint doesn't introduce one, so no schema anywhere returns it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import AssetStatus, StorageProvider


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    asset_type: str
    filename: str
    original_filename: str | None
    mime_type: str
    content_type: str | None
    size_bytes: int
    checksum: str
    etag: str | None
    storage_backend: StorageProvider
    status: AssetStatus
    uploaded_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
