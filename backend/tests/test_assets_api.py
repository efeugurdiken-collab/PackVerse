"""Tests for the Asset API (Sprint P4): upload, list, detail, download,
delete - plus the storage/database consistency guarantees the service
layer promises (rollback-removes-storage-object, idempotent delete,
storage-failure-leaves-db-untouched).

Uses the `client` fixture's isolated storage_backend (a fresh
LocalStorageBackend rooted in a per-test tmp_path - see conftest.py) so
uploads never touch the real ./data/storage volume.
"""
from __future__ import annotations

import hashlib
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.asset import Asset
from app.models.enums import AssetStatus, StorageProvider, UserRole
from app.services import asset_service
from app.services.asset_service import build_storage_key, sanitize_filename
from app.storage.exceptions import StorageDeleteFailed

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 32


def _files(content: bytes, filename: str = "test.png", content_type: str = "image/png") -> dict:
    return {"file": (filename, content, content_type)}


async def _upload(
    client,
    headers: dict[str, str],
    product_id,
    *,
    content: bytes = PNG_BYTES,
    filename: str = "test.png",
    content_type: str = "image/png",
    asset_type: str = "file",
):
    return await client.post(
        f"/api/v1/products/{product_id}/assets",
        files=_files(content, filename, content_type),
        data={"asset_type": asset_type},
        headers=headers,
    )


@pytest.fixture
async def operator_headers(make_user, auth_headers) -> dict[str, str]:
    user = await make_user(role=UserRole.OPERATOR)
    return auth_headers(user)


@pytest.fixture
async def admin_headers(make_user, auth_headers) -> dict[str, str]:
    user = await make_user(role=UserRole.ADMIN)
    return auth_headers(user)


@pytest.fixture
async def viewer_headers(make_user, auth_headers) -> dict[str, str]:
    user = await make_user(role=UserRole.VIEWER)
    return auth_headers(user)


@pytest.fixture
async def product(make_product):
    return await make_product()


# --- Upload ---------------------------------------------------------------


async def test_operator_can_upload_asset(client, operator_headers, product) -> None:
    response = await _upload(client, operator_headers, product.id)
    assert response.status_code == 201
    body = response.json()
    assert body["product_id"] == str(product.id)
    assert body["status"] == "available"
    assert body["storage_backend"] == "local"
    assert body["size_bytes"] == len(PNG_BYTES)
    assert body["checksum"] == hashlib.sha256(PNG_BYTES).hexdigest()
    assert uuid.UUID(body["id"])


async def test_admin_can_upload_asset(client, admin_headers, product) -> None:
    response = await _upload(client, admin_headers, product.id)
    assert response.status_code == 201


async def test_viewer_cannot_upload_asset(client, viewer_headers, product) -> None:
    response = await _upload(client, viewer_headers, product.id)
    assert response.status_code == 403


async def test_unauthenticated_cannot_upload_asset(client, product) -> None:
    response = await client.post(
        f"/api/v1/products/{product.id}/assets", files=_files(PNG_BYTES)
    )
    assert response.status_code == 401


async def test_upload_to_unknown_product_returns_404(client, operator_headers) -> None:
    response = await _upload(client, operator_headers, uuid.uuid4())
    assert response.status_code == 404


async def test_upload_empty_file_is_rejected(client, operator_headers, product) -> None:
    response = await _upload(client, operator_headers, product.id, content=b"")
    assert response.status_code == 422


async def test_upload_oversized_file_is_rejected(client, operator_headers, product) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    original = settings.max_upload_size_bytes
    settings.max_upload_size_bytes = 10
    try:
        response = await _upload(client, operator_headers, product.id, content=PNG_BYTES)
    finally:
        settings.max_upload_size_bytes = original
    assert response.status_code == 413


async def test_upload_unsupported_mime_type_is_rejected(client, operator_headers, product) -> None:
    response = await _upload(
        client,
        operator_headers,
        product.id,
        content=b"just some text",
        filename="notes.txt",
        content_type="text/plain",
    )
    assert response.status_code == 415


async def test_upload_checksum_is_persisted_and_matches_content(
    client, operator_headers, product
) -> None:
    response = await _upload(client, operator_headers, product.id, content=JPEG_BYTES,
                              filename="photo.jpg", content_type="image/jpeg")
    assert response.status_code == 201
    assert response.json()["checksum"] == hashlib.sha256(JPEG_BYTES).hexdigest()


async def test_duplicate_filename_uploads_do_not_collide(
    client, operator_headers, product
) -> None:
    first = await _upload(
        client, operator_headers, product.id, content=PNG_BYTES, filename="logo.png"
    )
    second = await _upload(
        client, operator_headers, product.id, content=JPEG_BYTES, filename="logo.png",
        content_type="image/jpeg",
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]

    first_download = await client.get(
        f"/api/v1/assets/{first.json()['id']}/download", headers=operator_headers
    )
    second_download = await client.get(
        f"/api/v1/assets/{second.json()['id']}/download", headers=operator_headers
    )
    assert first_download.content == PNG_BYTES
    assert second_download.content == JPEG_BYTES


async def test_database_rollback_removes_stored_object(
    db_session, storage_backend, make_product, make_user
) -> None:
    """If the post-storage-write database commit fails, the just-written
    storage object must not be left behind (see asset_service.upload_asset's
    IntegrityError branch)."""
    product = await make_product()
    uploader = await make_user(role=UserRole.OPERATOR)

    colliding_id = uuid.uuid4()
    pre_existing = Asset(
        id=colliding_id,
        product_id=product.id,
        asset_type="file",
        filename="existing.png",
        storage_key=f"products/{product.id}/{colliding_id}/existing.png",
        mime_type="image/png",
        size_bytes=1,
        checksum="deadbeef",
        storage_backend=StorageProvider.LOCAL,
        status=AssetStatus.AVAILABLE,
    )
    db_session.add(pre_existing)
    await db_session.commit()

    with patch("app.services.asset_service.uuid.uuid4", return_value=colliding_id):
        with pytest.raises(IntegrityError):
            await asset_service.upload_asset(
                db_session,
                storage_backend,
                product_id=product.id,
                asset_type="file",
                original_filename="new-upload.png",
                content_type="image/png",
                content=PNG_BYTES,
                uploaded_by_user_id=uploader.id,
            )

    new_key = f"products/{product.id}/{colliding_id}/new-upload.png"
    assert await storage_backend.exists(new_key) is False


# --- Listing and detail -----------------------------------------------------


async def test_list_assets_for_product(client, operator_headers, product) -> None:
    await _upload(client, operator_headers, product.id, filename="one.png")
    await _upload(client, operator_headers, product.id, filename="two.png")

    response = await client.get(
        f"/api/v1/products/{product.id}/assets", headers=operator_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


async def test_list_assets_pagination(client, operator_headers, product) -> None:
    for i in range(5):
        await _upload(client, operator_headers, product.id, filename=f"file-{i}.png")

    first_page = await client.get(
        f"/api/v1/products/{product.id}/assets",
        params={"limit": 2, "offset": 0},
        headers=operator_headers,
    )
    second_page = await client.get(
        f"/api/v1/products/{product.id}/assets",
        params={"limit": 2, "offset": 2},
        headers=operator_headers,
    )
    assert len(first_page.json()["items"]) == 2
    assert len(second_page.json()["items"]) == 2
    first_ids = {i["id"] for i in first_page.json()["items"]}
    second_ids = {i["id"] for i in second_page.json()["items"]}
    assert first_ids.isdisjoint(second_ids)


async def test_deleted_assets_excluded_from_listing(client, operator_headers, product) -> None:
    uploaded = await _upload(client, operator_headers, product.id)
    asset_id = uploaded.json()["id"]

    await client.delete(f"/api/v1/assets/{asset_id}", headers=operator_headers)

    response = await client.get(
        f"/api/v1/products/{product.id}/assets", headers=operator_headers
    )
    assert response.json()["total"] == 0


async def test_get_asset_returns_metadata(client, operator_headers, product) -> None:
    uploaded = await _upload(client, operator_headers, product.id)
    asset_id = uploaded.json()["id"]

    response = await client.get(f"/api/v1/assets/{asset_id}", headers=operator_headers)
    assert response.status_code == 200
    assert response.json()["id"] == asset_id


async def test_get_unknown_asset_returns_404(client, operator_headers) -> None:
    response = await client.get(f"/api/v1/assets/{uuid.uuid4()}", headers=operator_headers)
    assert response.status_code == 404


# --- Download ---------------------------------------------------------------


async def test_download_local_asset_streams_correct_content(
    client, operator_headers, product
) -> None:
    uploaded = await _upload(client, operator_headers, product.id, content=PNG_BYTES)
    asset_id = uploaded.json()["id"]

    response = await client.get(f"/api/v1/assets/{asset_id}/download", headers=operator_headers)
    assert response.status_code == 200
    assert response.content == PNG_BYTES


async def test_download_has_correct_headers(client, operator_headers, product) -> None:
    uploaded = await _upload(
        client, operator_headers, product.id, content=JPEG_BYTES,
        filename="photo.jpg", content_type="image/jpeg",
    )
    asset_id = uploaded.json()["id"]

    response = await client.get(f"/api/v1/assets/{asset_id}/download", headers=operator_headers)
    assert response.headers["content-type"] == "image/jpeg"
    assert "attachment" in response.headers["content-disposition"]
    assert uploaded.json()["filename"] in response.headers["content-disposition"]


async def test_download_deleted_asset_is_rejected(client, operator_headers, product) -> None:
    uploaded = await _upload(client, operator_headers, product.id)
    asset_id = uploaded.json()["id"]
    await client.delete(f"/api/v1/assets/{asset_id}", headers=operator_headers)

    response = await client.get(f"/api/v1/assets/{asset_id}/download", headers=operator_headers)
    assert response.status_code == 404


async def test_download_missing_storage_object_returns_502(
    client, operator_headers, product, storage_backend
) -> None:
    uploaded = await _upload(client, operator_headers, product.id)
    body = uploaded.json()

    # Simulate the object having vanished from storage without the
    # database being told - the DB row still says AVAILABLE.
    result = await client.get(f"/api/v1/assets/{body['id']}", headers=operator_headers)
    storage_key_guess_ok = result.status_code == 200  # sanity: asset really exists first
    assert storage_key_guess_ok

    # We don't have the raw storage_key (never exposed via the API by
    # design), so remove the object the same way the API itself would
    # have written it: reconstruct via the same key-building rule.
    safe_filename = sanitize_filename("test.png")
    key = build_storage_key(
        product_id=product.id, asset_id=uuid.UUID(body["id"]), safe_filename=safe_filename
    )
    await storage_backend.delete(key)

    response = await client.get(
        f"/api/v1/assets/{body['id']}/download", headers=operator_headers
    )
    assert response.status_code == 502


# --- Delete -------------------------------------------------------------


async def test_operator_can_delete_asset(client, operator_headers, product) -> None:
    uploaded = await _upload(client, operator_headers, product.id)
    asset_id = uploaded.json()["id"]

    response = await client.delete(f"/api/v1/assets/{asset_id}", headers=operator_headers)
    assert response.status_code == 204

    follow_up = await client.get(f"/api/v1/assets/{asset_id}", headers=operator_headers)
    assert follow_up.status_code == 404


async def test_admin_can_delete_asset(client, admin_headers, operator_headers, product) -> None:
    uploaded = await _upload(client, operator_headers, product.id)
    asset_id = uploaded.json()["id"]

    response = await client.delete(f"/api/v1/assets/{asset_id}", headers=admin_headers)
    assert response.status_code == 204


async def test_viewer_cannot_delete_asset(
    client, operator_headers, viewer_headers, product
) -> None:
    uploaded = await _upload(client, operator_headers, product.id)
    asset_id = uploaded.json()["id"]

    response = await client.delete(f"/api/v1/assets/{asset_id}", headers=viewer_headers)
    assert response.status_code == 403


async def test_delete_is_idempotent(client, operator_headers, product) -> None:
    uploaded = await _upload(client, operator_headers, product.id)
    asset_id = uploaded.json()["id"]

    first = await client.delete(f"/api/v1/assets/{asset_id}", headers=operator_headers)
    second = await client.delete(f"/api/v1/assets/{asset_id}", headers=operator_headers)
    assert first.status_code == 204
    assert second.status_code == 204


async def test_delete_storage_failure_leaves_asset_untouched(
    client, operator_headers, product, storage_backend
) -> None:
    uploaded = await _upload(client, operator_headers, product.id)
    asset_id = uploaded.json()["id"]

    async def _boom(key: str) -> None:
        raise StorageDeleteFailed(key, "simulated failure")

    with patch.object(storage_backend, "delete", _boom):
        response = await client.delete(f"/api/v1/assets/{asset_id}", headers=operator_headers)
    assert response.status_code == 502

    # The asset must not have been marked deleted - it's still readable.
    follow_up = await client.get(f"/api/v1/assets/{asset_id}", headers=operator_headers)
    assert follow_up.status_code == 200
    assert follow_up.json()["status"] == "available"
