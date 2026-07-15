"""JWT token API schemas."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import UserRole


class TokenPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenRefresh(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1)


class AccessTokenPayload(BaseModel):
    """Typed view of a decoded access token's claims - not sent over the
    wire; used internally by app/api/deps.py after app.core.security's
    decode_token() has already verified signature/expiry/type."""

    model_config = ConfigDict(extra="ignore")

    sub: uuid.UUID
    role: UserRole
    type: str
    iat: int
    exp: int
    jti: uuid.UUID
