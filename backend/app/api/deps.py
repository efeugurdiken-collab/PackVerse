"""Reusable FastAPI authorization dependencies.

get_current_user extracts and validates the bearer access token and loads
the corresponding user. require_active_user additionally rejects accounts
that are not ACTIVE (disabled or still pending). require_roles(...)
builds a dependency restricting access to a specific set of roles - used
to implement the Product API's viewer/operator/admin access matrix.

Every failure here returns 401 (not authenticated / token invalid) except
role/status checks, which return 403 (authenticated, but not permitted) -
that split is deliberate and expected by the test suite.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenError, TokenType, decode_token
from app.database.session import get_db
from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.services.exceptions import UserNotFoundError
from app.services.user_service import get_user_by_id

_bearer_scheme = HTTPBearer(auto_error=True)

_credentials_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
    except TokenError as exc:
        raise _credentials_error from exc

    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise _credentials_error from exc

    try:
        return await get_user_by_id(db, user_id)
    except UserNotFoundError as exc:
        raise _credentials_error from exc


async def require_active_user(user: User = Depends(get_current_user)) -> User:
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account is not active")
    return user


def require_roles(*allowed_roles: UserRole) -> Callable[..., Coroutine[Any, Any, User]]:
    """Returns a FastAPI dependency admitting only the given roles.

    Returns Callable[..., Coroutine[Any, Any, User]] rather than a fully
    parameterized signature because the dependency it builds is consumed
    exclusively via FastAPI's `Depends(...)`, which resolves the inner
    `user` parameter through its own reflection - there is no narrower
    type that both satisfies that reflection and mypy --strict here.
    """

    async def _dependency(user: User = Depends(require_active_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient role for this action",
            )
        return user

    return _dependency
