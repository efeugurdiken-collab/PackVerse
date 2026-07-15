"""Authentication endpoints: register, login, refresh, me.

Sprint P3 scope: no OAuth providers, no email verification delivery, no
password reset - see the sprint spec's Security Rules for the explicit
exclusions.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import (
    TokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.database.session import get_db
from app.models.enums import UserStatus
from app.models.user import User
from app.schemas.token import TokenPair, TokenRefresh
from app.schemas.user import UserLogin, UserRead, UserRegister
from app.services import user_service
from app.services.exceptions import DuplicateEmailError, InvalidCredentialsError, UserNotFoundError

router = APIRouter(prefix="/auth", tags=["auth"])

_invalid_credentials_response = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="invalid credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

_invalid_refresh_response = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="invalid refresh token",
    headers={"WWW-Authenticate": "Bearer"},
)


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(payload: UserRegister, db: AsyncSession = Depends(get_db)) -> UserRead:
    try:
        user = await user_service.register_user(db, payload)
    except DuplicateEmailError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return UserRead.model_validate(user)


@router.post("/login", response_model=TokenPair)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)) -> TokenPair:
    try:
        user = await user_service.authenticate_user(
            db, email=payload.email, password=payload.password
        )
    except InvalidCredentialsError as exc:
        raise _invalid_credentials_response from exc

    user = await user_service.record_login(db, user)

    return TokenPair(
        access_token=create_access_token(subject=user.id, role=user.role.value),
        refresh_token=create_refresh_token(subject=user.id, role=user.role.value),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: TokenRefresh, db: AsyncSession = Depends(get_db)) -> TokenPair:
    try:
        claims = decode_token(payload.refresh_token, expected_type=TokenType.REFRESH)
    except TokenError as exc:
        raise _invalid_refresh_response from exc

    try:
        subject_id = uuid.UUID(str(claims["sub"]))
        user = await user_service.get_user_by_id(db, subject_id)
    except (UserNotFoundError, ValueError) as exc:
        # ValueError: claims["sub"] wasn't a well-formed UUID - treat the
        # same as "user not found" rather than a 500.
        raise _invalid_refresh_response from exc

    if user.status != UserStatus.ACTIVE:
        raise _invalid_refresh_response

    # Rotated: every refresh call re-derives the role from the current
    # database row (not the possibly-stale role claim in the presented
    # refresh token) and issues a brand new token pair, so a role change
    # takes effect on the user's very next refresh rather than waiting
    # for the old refresh token to expire.
    return TokenPair(
        access_token=create_access_token(subject=user.id, role=user.role.value),
        refresh_token=create_refresh_token(subject=user.id, role=user.role.value),
    )


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(user)
