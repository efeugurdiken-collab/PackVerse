"""User service layer - registration, authentication, and lookup.

Mirrors the structure of app/services/product_service.py: all database
access lives here, the API layer only translates domain exceptions to
HTTP responses.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, normalize_email, verify_password
from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.schemas.user import UserRegister
from app.services.exceptions import DuplicateEmailError, InvalidCredentialsError, UserNotFoundError


async def register_user(db: AsyncSession, data: UserRegister) -> User:
    """Creates a user with a client-supplied password but a
    server-controlled role and status - UserRegister has no role/status/
    is_verified fields at all (see app/schemas/user.py), so there is
    nothing for a client to escalate here even before this function runs.
    """
    user = User(
        email=normalize_email(data.email),
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
        role=UserRole.VIEWER,
        status=UserStatus.ACTIVE,
        is_verified=False,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "ix_users_email" in str(exc.orig) or "users_email" in str(exc.orig):
            raise DuplicateEmailError(data.email) from exc
        raise
    await db.refresh(user)
    return user


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise UserNotFoundError(user_id)
    return user


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def authenticate_user(db: AsyncSession, *, email: str, password: str) -> User:
    """Verifies credentials and account status in one place so every
    failure path - unknown email, wrong password, disabled account -
    raises the same InvalidCredentialsError. See that exception's
    docstring for why: distinguishing the reasons in the response would
    let an attacker enumerate registered emails or account states.
    """
    user = await get_user_by_email(db, email)
    if user is None:
        # Still runs a hash comparison against a dummy value so this path
        # takes roughly the same time as a real mismatch - otherwise a
        # timing difference between "no such user" and "wrong password"
        # becomes its own enumeration side-channel.
        verify_password(password, _DUMMY_HASH)
        raise InvalidCredentialsError()

    if not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError()

    if user.status != UserStatus.ACTIVE:
        raise InvalidCredentialsError()

    return user


async def record_login(db: AsyncSession, user: User) -> User:
    user.last_login_at = datetime.now(timezone.utc)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# A real Argon2id hash of an unguessable, never-used password - computed
# once at import time (not per-request, which would add its own latency
# to every process start but nothing per-login) - purely so the "unknown
# email" branch above runs a genuine, full-cost Argon2 verification
# instead of short-circuiting, keeping its timing close to a real
# wrong-password failure.
_DUMMY_HASH = hash_password(uuid.uuid4().hex)
