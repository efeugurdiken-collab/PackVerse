"""Product API schemas.

Timestamps (created_at, updated_at) and id are server-controlled and
therefore only ever appear in *Read schemas - never in Create/Update
schemas, so a client cannot set or override them.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ProductStatus, ProductType


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=255, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    product_type: ProductType
    price_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    metadata_json: dict[str, object] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def currency_must_be_uppercase_alpha(cls, v: str) -> str:
        if not v.isalpha() or not v.isupper():
            raise ValueError("currency must be a 3-letter uppercase ISO 4217 code, e.g. 'USD'")
        return v


class ProductUpdate(BaseModel):
    """All fields optional - PATCH semantics. slug, product_type, and version
    are intentionally excluded: they are immutable after creation in this
    sprint (slug is a stable identifier, product_type defines the catalog
    line, version is bumped by the future Publishing workflow, not the client).
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    status: ProductStatus | None = None
    price_cents: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    metadata_json: dict[str, object] | None = None

    @field_validator("currency")
    @classmethod
    def currency_must_be_uppercase_alpha(cls, v: str | None) -> str | None:
        if v is not None and (not v.isalpha() or not v.isupper()):
            raise ValueError("currency must be a 3-letter uppercase ISO 4217 code, e.g. 'USD'")
        return v


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    description: str | None
    product_type: ProductType
    status: ProductStatus
    version: str
    price_cents: int
    currency: str
    metadata_json: dict[str, object]
    created_at: datetime
    updated_at: datetime
