"""Tests for the Product API's authorization matrix (Sprint P3).

viewer: read-only. operator: create, read, update. admin: everything
operator can do (no separate admin-only action exists yet, since there is
still no delete endpoint - "full current access" means "same as operator"
until a delete or admin-only endpoint is added in a later sprint).
"""
import uuid

import pytest

from app.models.enums import UserRole, UserStatus

BASE = "/api/v1/products"


def _payload(**overrides: object) -> dict:
    payload = {
        "slug": f"svg-pack-{uuid.uuid4().hex[:8]}",
        "title": "SVG Pack: Shapes",
        "description": None,
        "product_type": "svg_pack",
        "price_cents": 1500,
        "currency": "USD",
        "metadata_json": {},
    }
    payload.update(overrides)
    return payload


@pytest.fixture
async def viewer(make_user):
    return await make_user(role=UserRole.VIEWER)


@pytest.fixture
async def operator(make_user):
    return await make_user(role=UserRole.OPERATOR)


@pytest.fixture
async def admin(make_user):
    return await make_user(role=UserRole.ADMIN)


@pytest.fixture
async def existing_product(client, make_user, auth_headers):
    creator = await make_user(role=UserRole.OPERATOR)
    response = await client.post(BASE, json=_payload(), headers=auth_headers(creator))
    assert response.status_code == 201
    return response.json()


# --- Unauthenticated ---------------------------------------------------


async def test_unauthenticated_list_returns_401(client) -> None:
    response = await client.get(BASE)
    assert response.status_code == 401


async def test_unauthenticated_create_returns_401(client) -> None:
    response = await client.post(BASE, json=_payload())
    assert response.status_code == 401


# --- Viewer: read-only ---------------------------------------------------


async def test_viewer_can_list_products(client, viewer, auth_headers, existing_product) -> None:
    response = await client.get(BASE, headers=auth_headers(viewer))
    assert response.status_code == 200


async def test_viewer_can_get_product(client, viewer, auth_headers, existing_product) -> None:
    response = await client.get(f"{BASE}/{existing_product['id']}", headers=auth_headers(viewer))
    assert response.status_code == 200


async def test_viewer_cannot_create_product(client, viewer, auth_headers) -> None:
    response = await client.post(BASE, json=_payload(), headers=auth_headers(viewer))
    assert response.status_code == 403


async def test_viewer_cannot_update_product(client, viewer, auth_headers, existing_product) -> None:
    response = await client.patch(
        f"{BASE}/{existing_product['id']}",
        json={"title": "Hijacked"},
        headers=auth_headers(viewer),
    )
    assert response.status_code == 403


# --- Operator: create, read, update ---------------------------------------


async def test_operator_can_create_product(client, operator, auth_headers) -> None:
    response = await client.post(BASE, json=_payload(), headers=auth_headers(operator))
    assert response.status_code == 201


async def test_operator_can_read_product(client, operator, auth_headers, existing_product) -> None:
    response = await client.get(f"{BASE}/{existing_product['id']}", headers=auth_headers(operator))
    assert response.status_code == 200


async def test_operator_can_update_product(client, operator, auth_headers, existing_product) -> None:
    response = await client.patch(
        f"{BASE}/{existing_product['id']}",
        json={"title": "Updated by operator"},
        headers=auth_headers(operator),
    )
    assert response.status_code == 200


# --- Admin: full current access -------------------------------------------


async def test_admin_can_create_read_and_update_product(
    client, admin, auth_headers
) -> None:
    create_response = await client.post(BASE, json=_payload(), headers=auth_headers(admin))
    assert create_response.status_code == 201
    product_id = create_response.json()["id"]

    read_response = await client.get(f"{BASE}/{product_id}", headers=auth_headers(admin))
    assert read_response.status_code == 200

    update_response = await client.patch(
        f"{BASE}/{product_id}", json={"title": "Updated by admin"}, headers=auth_headers(admin)
    )
    assert update_response.status_code == 200


# --- Disabled account -------------------------------------------------


async def test_disabled_user_cannot_access_product_api(client, make_user, auth_headers) -> None:
    disabled_user = await make_user(role=UserRole.ADMIN, status=UserStatus.DISABLED)
    response = await client.get(BASE, headers=auth_headers(disabled_user))
    assert response.status_code == 403
