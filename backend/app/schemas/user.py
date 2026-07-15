"""User API schemas.

UserRegister has no role/status/is_verified fields at all - not merely
defaults that get overwritten server-side - so there is no field name a
client could set to attempt privilege escalation during registration, and
`extra="forbid"` rejects the attempt outright if they try anyway.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.config import get_settings
from app.models.enums import UserRole, UserStatus


class UserRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    full_name: str = Field(min_length=1, max_length=255)

    @field_validator("password")
    @classmethod
    def enforce_password_policy(cls, v: str) -> str:
        min_length = get_settings().min_password_length
        if len(v) < min_length:
            raise ValueError(f"password must be at least {min_length} characters")
        if v.isalpha() or v.isdigit():
            raise ValueError("password must mix letters, numbers, or symbols - not just one kind")
        return v


class UserLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserRead(BaseModel):
    """hashed_password is never included - this schema defines the
    complete set of fields a client is allowed to see about a user."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    status: UserStatus
    is_verified: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
