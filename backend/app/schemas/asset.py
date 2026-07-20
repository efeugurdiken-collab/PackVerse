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

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class AssetIngestionCreate(BaseModel):
    """POST /assets/{asset_id}/ingest request body (Sprint P10B3). The
    caller supplies which embedding model/provider to use - there is no
    server-side default (app.services.ingestion_service.ingest_asset's
    embedding_model has no default value either) - plus optional
    chunk-size overrides passed straight through to
    app.rag.chunking.chunk_text via app.jobs.service.
    enqueue_asset_ingestion.

    chunk_overlap < chunk_size is validated here, synchronously, rather
    than left to fail inside the worker: chunk_text() itself only raises
    a plain ValueError for an invalid pair, and
    app/worker/dispatch.py's terminal-vs-retryable classification has no
    "bad request" category of its own for asset_ingestion jobs (a plain
    ValueError would fall through to the generic Exception branch and be
    retried as if it were a transient infrastructure failure, then fail
    the same way on every subsequent attempt) - failing the POST itself
    with 422 is both more correct and more useful to the caller than a
    job that is doomed to fail after using up its retry budget.
    """

    model_config = ConfigDict(extra="forbid")

    embedding_model: str = Field(min_length=1)
    embedding_provider: str | None = None
    chunk_size: int = Field(default=1000, gt=0)
    chunk_overlap: int = Field(default=200, ge=0)

    @model_validator(mode="after")
    def validate_chunk_overlap_smaller_than_chunk_size(self) -> "AssetIngestionCreate":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self
