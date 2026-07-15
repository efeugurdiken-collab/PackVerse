"""Tests for registration, login, and the JWT access/refresh token flow.

Endpoints: POST /api/v1/auth/{register,login,refresh}, GET /api/v1/auth/me.
"""
import time
import uuid

import jwt

from app.core.config import get_settings
from app.core.security import TokenType, create_access_token, create_refresh_token
from app.models.enums import UserStatus
from app.services.user_service import get_user_by_email

BASE = "/api/v1/auth"


def _register_payload(**overrides: object) -> dict:
    payload = {
        "email": f"user-{uuid.uuid4().hex[:10]}@example.com",
        "password": "a-perfectly-fine-passw0rd",
        "full_name": "Ada Lovelace",
    }
    payload.update(overrides)
    return payload


# --- Registration ---------------------------------------------------------


async def test_register_succeeds_and_returns_safe_user_data(client) -> None:
    response = await client.post(f"{BASE}/register", json=_register_payload())
    assert response.status_code == 201
    body = response.json()

    assert body["role"] == "viewer"
    assert body["status"] == "active"
    assert body["is_verified"] is False
    assert "hashed_password" not in body
    assert "password" not in body


async def test_register_duplicate_email_returns_409(client) -> None:
    payload = _register_payload()
    first = await client.post(f"{BASE}/register", json=payload)
    assert first.status_code == 201

    second = await client.post(f"{BASE}/register", json=payload)
    assert second.status_code == 409


async def test_register_normalizes_email(client, db_session) -> None:
    payload = _register_payload(email="Mixed.Case@Example.COM")
    response = await client.post(f"{BASE}/register", json=payload)
    assert response.status_code == 201
    assert response.json()["email"] == "mixed.case@example.com"


async def test_register_password_is_not_stored_in_plaintext(client, db_session) -> None:
    payload = _register_payload()
    response = await client.post(f"{BASE}/register", json=payload)
    assert response.status_code == 201

    user = await get_user_by_email(db_session, payload["email"])
    assert user is not None
    assert user.hashed_password != payload["password"]
    assert user.hashed_password.startswith("$argon2id$")


async def test_register_rejects_client_supplied_role(client) -> None:
    payload = _register_payload(role="admin")
    response = await client.post(f"{BASE}/register", json=payload)
    # extra="forbid" on UserRegister rejects the unknown "role" field.
    assert response.status_code == 422


async def test_register_rejects_short_password(client) -> None:
    payload = _register_payload(password="short1")
    response = await client.post(f"{BASE}/register", json=payload)
    assert response.status_code == 422


# --- Login -----------------------------------------------------------------


async def test_login_succeeds_and_returns_token_pair(client) -> None:
    payload = _register_payload()
    await client.post(f"{BASE}/register", json=payload)

    response = await client.post(
        f"{BASE}/login", json={"email": payload["email"], "password": payload["password"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]


async def test_login_with_wrong_password_returns_401(client) -> None:
    payload = _register_payload()
    await client.post(f"{BASE}/register", json=payload)

    response = await client.post(
        f"{BASE}/login", json={"email": payload["email"], "password": "wrong-password-entirely"}
    )
    assert response.status_code == 401


async def test_login_with_unknown_email_returns_401(client) -> None:
    response = await client.post(
        f"{BASE}/login",
        json={"email": "nobody-registered@example.com", "password": "whatever12345"},
    )
    assert response.status_code == 401


async def test_login_rejects_disabled_user(client, make_user) -> None:
    user = await make_user(
        email="disabled@example.com", password="a-perfectly-fine-passw0rd", status=UserStatus.DISABLED
    )
    response = await client.post(
        f"{BASE}/login", json={"email": user.email, "password": "a-perfectly-fine-passw0rd"}
    )
    assert response.status_code == 401


async def test_login_updates_last_login_at(client, db_session) -> None:
    payload = _register_payload()
    await client.post(f"{BASE}/register", json=payload)

    before = await get_user_by_email(db_session, payload["email"])
    assert before is not None
    assert before.last_login_at is None

    response = await client.post(
        f"{BASE}/login", json={"email": payload["email"], "password": payload["password"]}
    )
    assert response.status_code == 200

    db_session.expire_all()
    after = await get_user_by_email(db_session, payload["email"])
    assert after is not None
    assert after.last_login_at is not None


# --- JWT ---------------------------------------------------------------


async def test_me_with_valid_access_token_returns_current_user(client, make_user, auth_headers) -> None:
    user = await make_user()
    response = await client.get(f"{BASE}/me", headers=auth_headers(user))
    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)


async def test_me_without_token_returns_401(client) -> None:
    response = await client.get(f"{BASE}/me")
    assert response.status_code == 401


async def test_me_with_expired_token_returns_401(client, make_user) -> None:
    user = await make_user()
    settings = get_settings()
    expired_payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "type": TokenType.ACCESS.value,
        "iat": int(time.time()) - 3600,
        "exp": int(time.time()) - 1800,
        "jti": str(uuid.uuid4()),
    }
    expired_token = jwt.encode(expired_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    response = await client.get(f"{BASE}/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert response.status_code == 401


async def test_me_with_bad_signature_returns_401(client, make_user) -> None:
    user = await make_user()
    bogus_token = jwt.encode(
        {
            "sub": str(user.id),
            "role": user.role.value,
            "type": TokenType.ACCESS.value,
            "iat": int(time.time()),
            "exp": int(time.time()) + 900,
            "jti": str(uuid.uuid4()),
        },
        "a-completely-different-and-wrong-secret-key-value",
        algorithm="HS256",
    )
    response = await client.get(f"{BASE}/me", headers={"Authorization": f"Bearer {bogus_token}"})
    assert response.status_code == 401


async def test_refresh_flow_issues_new_token_pair(client, make_user) -> None:
    user = await make_user()
    refresh_token = create_refresh_token(subject=user.id, role=user.role.value)

    response = await client.post(f"{BASE}/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]


async def test_refresh_rejects_an_access_token(client, make_user) -> None:
    user = await make_user()
    access_token = create_access_token(subject=user.id, role=user.role.value)

    response = await client.post(f"{BASE}/refresh", json={"refresh_token": access_token})
    assert response.status_code == 401


async def test_me_rejects_a_refresh_token(client, make_user) -> None:
    user = await make_user()
    refresh_token = create_refresh_token(subject=user.id, role=user.role.value)

    response = await client.get(f"{BASE}/me", headers={"Authorization": f"Bearer {refresh_token}"})
    assert response.status_code == 401
