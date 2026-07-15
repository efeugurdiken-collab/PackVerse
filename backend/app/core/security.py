"""Security primitives: password hashing and JWT issuance/verification.

Deliberately pure - no database access and no FastAPI imports - so this
module can be unit tested in isolation and reused by both the auth
endpoints (app/api/v1/auth.py) and the authorization dependencies
(app/api/deps.py) without either depending on the other.

Password hashing uses Argon2id (via argon2-cffi's high-level PasswordHasher,
which defaults to the argon2id variant) per the Sprint P3 spec's explicit
preference over bcrypt/scrypt/PBKDF2. Argon2 hashes are self-describing
(the returned string encodes algorithm, version, and cost parameters), so
verification and rehash-on-parameter-change are both handled by the
library rather than needing a separate stored "scheme" column.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash

from app.core.config import get_settings

_password_hasher = PasswordHasher()


class TokenType(str, enum.Enum):
    ACCESS = "access"
    REFRESH = "refresh"


class TokenError(Exception):
    """Raised for any invalid, expired, malformed, or wrong-type token.

    Deliberately a single exception type: the auth endpoints and
    dependencies that catch this always respond with the same generic
    401, so callers don't need to distinguish "expired" from "malformed"
    from "wrong type" - doing so would leak information useful to an
    attacker probing the token validation logic.
    """


def hash_password(plain_password: str) -> str:
    return _password_hasher.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        _password_hasher.verify(hashed_password, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False
    return True


def normalize_email(email: str) -> str:
    """Lowercase + strip, so 'User@Example.com ' and 'user@example.com'
    collide on the unique index instead of creating two accounts."""
    return email.strip().lower()


def _create_token(
    *, subject: uuid.UUID, role: str, token_type: TokenType, expires_delta: timedelta
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "role": role,
        "type": token_type.value,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(*, subject: uuid.UUID, role: str) -> str:
    settings = get_settings()
    return _create_token(
        subject=subject,
        role=role,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(*, subject: uuid.UUID, role: str) -> str:
    settings = get_settings()
    return _create_token(
        subject=subject,
        role=role,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]:
    """Decodes and validates a token's signature, expiration, and type.

    Raises TokenError for anything wrong - expired, bad signature,
    malformed, or right signature but wrong `type` claim (e.g. an access
    token presented to the refresh endpoint, or vice versa).
    """
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "role", "type", "iat", "exp", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("invalid or expired token") from exc

    if payload.get("type") != expected_type.value:
        raise TokenError(f"expected a {expected_type.value} token")

    return payload
