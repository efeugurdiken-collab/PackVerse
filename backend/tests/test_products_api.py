"""Tests for the Product API (POST/GET/GET-list/PATCH /api/v1/products).

Each test uses the `client` fixture from conftest.py, which wires HTTP
requests to an isolated, per-test database transaction, so tests can run
in any order without interfering with each other.
"""
import uuid

BASE = "/api/v1/products"


def _payload(**overrides: object) -> dict:
    payload = {
        "slug": f"prompt-pack-{uuid.uuid4().hex[:8]}",
        "title": "Prompt Pack: Marketing",
        "description": "50 prompts for marketing copy.",
        "product_type": "prompt_pack",
        "price_cents": 2900,
        "currency": "USD",
        "metadata_json": {"tags": ["marketing", "copywriting"]},
    }
    payload.update(overrides)
    return payload


async def test_create_product_returns_201_with_server_controlled_fields(client) -> None:
    response = await client.post(BASE, json=_payload())
    assert response.status_code == 201
    body = response.json()

    assert body["status"] == "draft"
    assert body["version"] == "v1.0"
    assert uuid.UUID(body["id"])
    assert "created_at" in body
    assert "updated_at" in body


async def test_get_product_returns_the_created_product(client) -> None:
    created = (await client.post(BASE, json=_payload(title="Prompt Pack: SEO"))).json()

    response = await client.get(f"{BASE}/{created['id']}")
    assert response.status_code == 200
    assert response.json()["title"] == "Prompt Pack: SEO"


async def test_get_unknown_product_returns_404(client) -> None:
    response = await client.get(f"{BASE}/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_create_product_with_duplicate_slug_returns_409(client) -> None:
    payload = _payload(slug="duplicate-slug-test")
    first = await client.post(BASE, json=payload)
    assert first.status_code == 201

    second = await client.post(BASE, json=payload)
    assert second.status_code == 409


async def test_list_products_pagination(client) -> None:
    for i in range(5):
        await client.post(BASE, json=_payload(title=f"Prompt Pack {i}"))

    first_page = await client.get(BASE, params={"limit": 2, "offset": 0})
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["items"]) == 2
    assert first_body["limit"] == 2
    assert first_body["offset"] == 0
    assert first_body["total"] >= 5

    second_page = await client.get(BASE, params={"limit": 2, "offset": 2})
    second_body = second_page.json()
    assert len(second_body["items"]) == 2

    first_ids = {item["id"] for item in first_body["items"]}
    second_ids = {item["id"] for item in second_body["items"]}
    assert first_ids.isdisjoint(second_ids)


async def test_update_product_applies_partial_changes(client) -> None:
    created = (await client.post(BASE, json=_payload())).json()

    response = await client.patch(
        f"{BASE}/{created['id']}", json={"title": "Updated Title", "price_cents": 3500}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Updated Title"
    assert body["price_cents"] == 3500
    # Untouched fields are preserved.
    assert body["slug"] == created["slug"]
    assert body["currency"] == created["currency"]


async def test_update_product_rejects_immutable_fields(client) -> None:
    created = (await client.post(BASE, json=_payload())).json()

    response = await client.patch(f"{BASE}/{created['id']}", json={"slug": "new-slug"})
    assert response.status_code == 422  # extra="forbid" rejects slug on ProductUpdate


async def test_update_unknown_product_returns_404(client) -> None:
    response = await client.patch(f"{BASE}/{uuid.uuid4()}", json={"title": "Nope"})
    assert response.status_code == 404


async def test_create_product_rejects_invalid_currency(client) -> None:
    response = await client.post(BASE, json=_payload(currency="usd"))
    assert response.status_code == 422
