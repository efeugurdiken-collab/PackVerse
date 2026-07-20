"""Tests for the Asset API (Sprint P4): upload, list, detail, download,
delete - plus the storage/database consistency guarantees the service
layer promises (rollback-removes-storage-object, idempotent delete,
storage-failure-leaves-db-untouched). Sprint P10B3 adds POST/GET
/assets/{asset_id}/ingest.

Uses the `client` fixture's isolated storage_backend (a fresh
LocalStorageBackend rooted in a per-test tmp_path - see conftest.py) so
uploads never touch the real ./data/storage volume.
"""
from __future__ import annotations

import hashlib
import uuid
import warnings
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError, SAWarning

from app.models.asset import Asset
from app.models.enums import AssetStatus, StorageProvider, UserRole
from app.models.product import Product
from app.services import asset_service
from app.services.asset_service import build_storage_key, sanitize_filename
from app.storage.exceptions import StorageDeleteFailed

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 32
TEXT_BYTES = b"hello world, this is a real ingestable document."


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
    # text/csv is deliberately never in allowed_mime_types_list - unlike
    # text/plain and text/markdown, which Sprint P10B2 added so a source
    # document can be uploaded and then ingested (see
    # app/services/ingestion_service.py).
    response = await _upload(
        client,
        operator_headers,
        product.id,
        content=b"just some text",
        filename="notes.csv",
        content_type="text/csv",
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


async def _seed_pk_collision(db_session, make_product, make_user) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Commits a real Asset row, then expunges the Python object from the
    session's identity map (without deleting the row) so that a second
    Asset object built with the same primary key produces a genuine
    database-level IntegrityError on flush.

    Without the expunge, a second in-memory Asset instance sharing the
    already-persistent instance's primary key is instead an in-session
    identity-map conflict, which SQLAlchemy flags with an SAWarning
    ("New instance ... conflicts with persistent instance ...") - a
    different, ORM-internal condition than the real-world case this test
    means to exercise (another process/commit already wrote this row).

    Returns plain (product_id, uploader_id, colliding_id) values, never
    the ORM instances themselves: any operation that triggers a rollback
    (as asset_service.upload_asset's IntegrityError branch does) expires
    every object still attached to db_session, and a later *synchronous*
    attribute access on an expired instance (e.g. inside an f-string)
    raises sqlalchemy.exc.MissingGreenlet, since the implicit refresh
    SELECT that expired access would trigger can only run inside an
    awaited call.
    """
    product = await make_product()
    uploader = await make_user(role=UserRole.OPERATOR)
    product_id, uploader_id = product.id, uploader.id

    colliding_id = uuid.uuid4()
    pre_existing = Asset(
        id=colliding_id,
        product_id=product_id,
        asset_type="file",
        filename="existing.png",
        storage_key=f"products/{product_id}/{colliding_id}/existing.png",
        mime_type="image/png",
        size_bytes=1,
        checksum="deadbeef",
        storage_backend=StorageProvider.LOCAL,
        status=AssetStatus.AVAILABLE,
    )
    db_session.add(pre_existing)
    await db_session.commit()
    db_session.expunge(pre_existing)
    return product_id, uploader_id, colliding_id


async def _attempt_colliding_upload(
    db_session, storage_backend, product_id: uuid.UUID, uploader_id: uuid.UUID, colliding_id: uuid.UUID
) -> None:
    with patch("app.services.asset_service.uuid.uuid4", return_value=colliding_id):
        with pytest.raises(IntegrityError):
            await asset_service.upload_asset(
                db_session,
                storage_backend,
                product_id=product_id,
                asset_type="file",
                original_filename="new-upload.png",
                content_type="image/png",
                content=PNG_BYTES,
                uploaded_by_user_id=uploader_id,
            )


async def test_database_rollback_removes_stored_object(
    db_session, storage_backend, make_product, make_user
) -> None:
    """If the post-storage-write database commit fails, the just-written
    storage object must not be left behind (see asset_service.upload_asset's
    IntegrityError branch)."""
    product_id, uploader_id, colliding_id = await _seed_pk_collision(
        db_session, make_product, make_user
    )
    await _attempt_colliding_upload(db_session, storage_backend, product_id, uploader_id, colliding_id)

    new_key = build_storage_key(
        product_id=product_id, asset_id=colliding_id, safe_filename="new-upload.png"
    )
    assert await storage_backend.exists(new_key) is False


async def test_session_remains_usable_after_upload_rollback(
    db_session, storage_backend, make_product, make_user
) -> None:
    """The session must not be left broken by the service layer's own
    controlled rollback - a plain, fully-awaited query (never an
    attribute access on a previously loaded, now-expired instance) must
    still succeed afterward."""
    product_id, uploader_id, colliding_id = await _seed_pk_collision(
        db_session, make_product, make_user
    )
    await _attempt_colliding_upload(db_session, storage_backend, product_id, uploader_id, colliding_id)

    refreshed = await db_session.get(Product, product_id)
    assert refreshed is not None
    assert refreshed.id == product_id


async def test_rollback_cleanup_uses_preserved_key_not_orm_attribute(
    db_session, storage_backend, make_product, make_user
) -> None:
    """The IntegrityError cleanup path must delete the storage object
    using the plain `storage_key` local variable captured before the
    failed commit, never by reading `asset.storage_key` back off the ORM
    object post-rollback."""
    product_id, uploader_id, colliding_id = await _seed_pk_collision(
        db_session, make_product, make_user
    )

    deleted_keys: list[str] = []
    real_delete = storage_backend.delete

    async def _spy_delete(key: str) -> None:
        deleted_keys.append(key)
        await real_delete(key)

    with patch.object(storage_backend, "delete", _spy_delete):
        await _attempt_colliding_upload(db_session, storage_backend, product_id, uploader_id, colliding_id)

    expected_key = build_storage_key(
        product_id=product_id, asset_id=colliding_id, safe_filename="new-upload.png"
    )
    assert deleted_keys == [expected_key]


async def test_rollback_cleanup_emits_no_identity_conflict_warning(
    db_session, storage_backend, make_product, make_user
) -> None:
    """Regression guard for the SAWarning ("New instance ... conflicts
    with persistent instance ...") that a same-session identity-key
    collision would otherwise emit - see _seed_pk_collision's expunge()."""
    product_id, uploader_id, colliding_id = await _seed_pk_collision(
        db_session, make_product, make_user
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await _attempt_colliding_upload(db_session, storage_backend, product_id, uploader_id, colliding_id)

    identity_conflicts = [
        w
        for w in caught
        if issubclass(w.category, SAWarning) and "conflicts with persistent instance" in str(w.message)
    ]
    assert identity_conflicts == []


async def test_rollback_preserves_original_integrity_error(
    db_session, storage_backend, make_product, make_user
) -> None:
    """The service layer re-raises the driver's own error via a bare
    `raise` inside the except block - the original IntegrityError must
    reach the caller unwrapped and unreplaced, not converted into a
    different domain/storage exception."""
    product_id, uploader_id, colliding_id = await _seed_pk_collision(
        db_session, make_product, make_user
    )

    with patch("app.services.asset_service.uuid.uuid4", return_value=colliding_id):
        with pytest.raises(IntegrityError) as excinfo:
            await asset_service.upload_asset(
                db_session,
                storage_backend,
                product_id=product_id,
                asset_type="file",
                original_filename="new-upload.png",
                content_type="image/png",
                content=PNG_BYTES,
                uploaded_by_user_id=uploader_id,
            )
    assert type(excinfo.value) is IntegrityError


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


# --- Ingestion (Sprint P10B3) ----------------------------------------------


async def _upload_text_asset(client, headers: dict[str, str], product_id) -> str:
    response = await _upload(
        client,
        headers,
        product_id,
        content=TEXT_BYTES,
        filename="doc.txt",
        content_type="text/plain",
    )
    assert response.status_code == 201
    return response.json()["id"]


def _ingest_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"embedding_model": "fake-embed-v1"}
    payload.update(overrides)
    return payload


async def test_operator_can_enqueue_ingestion(client, operator_headers, product) -> None:
    asset_id = await _upload_text_asset(client, operator_headers, product.id)

    response = await client.post(
        f"/api/v1/assets/{asset_id}/ingest",
        json=_ingest_payload(),
        headers=operator_headers,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["job_type"] == "asset_ingestion"
    assert body["status"] == "queued"
    assert body["input_json"]["embedding_model"] == "fake-embed-v1"
    assert body["input_json"]["chunk_size"] == 1000
    assert body["input_json"]["chunk_overlap"] == 200
    assert uuid.UUID(body["id"])


async def test_admin_can_enqueue_ingestion(client, admin_headers, operator_headers, product) -> None:
    asset_id = await _upload_text_asset(client, operator_headers, product.id)

    response = await client.post(
        f"/api/v1/assets/{asset_id}/ingest", json=_ingest_payload(), headers=admin_headers
    )
    assert response.status_code == 202


async def test_viewer_cannot_enqueue_ingestion(
    client, operator_headers, viewer_headers, product
) -> None:
    asset_id = await _upload_text_asset(client, operator_headers, product.id)

    response = await client.post(
        f"/api/v1/assets/{asset_id}/ingest", json=_ingest_payload(), headers=viewer_headers
    )
    assert response.status_code == 403


async def test_unauthenticated_cannot_enqueue_ingestion(
    client, operator_headers, product
) -> None:
    asset_id = await _upload_text_asset(client, operator_headers, product.id)

    response = await client.post(
        f"/api/v1/assets/{asset_id}/ingest", json=_ingest_payload()
    )
    assert response.status_code == 401


async def test_enqueue_ingestion_unknown_asset_returns_404(client, operator_headers) -> None:
    response = await client.post(
        f"/api/v1/assets/{uuid.uuid4()}/ingest", json=_ingest_payload(), headers=operator_headers
    )
    assert response.status_code == 404


async def test_enqueue_ingestion_unsupported_content_type_returns_415(
    client, operator_headers, product
) -> None:
    uploaded = await _upload(client, operator_headers, product.id)  # default: image/png
    asset_id = uploaded.json()["id"]

    response = await client.post(
        f"/api/v1/assets/{asset_id}/ingest", json=_ingest_payload(), headers=operator_headers
    )
    assert response.status_code == 415


async def test_enqueue_ingestion_twice_returns_409(client, operator_headers, product) -> None:
    asset_id = await _upload_text_asset(client, operator_headers, product.id)

    first = await client.post(
        f"/api/v1/assets/{asset_id}/ingest", json=_ingest_payload(), headers=operator_headers
    )
    second = await client.post(
        f"/api/v1/assets/{asset_id}/ingest", json=_ingest_payload(), headers=operator_headers
    )
    assert first.status_code == 202
    assert second.status_code == 409


async def test_enqueue_ingestion_missing_embedding_model_returns_422(
    client, operator_headers, product
) -> None:
    asset_id = await _upload_text_asset(client, operator_headers, product.id)

    response = await client.post(
        f"/api/v1/assets/{asset_id}/ingest", json={}, headers=operator_headers
    )
    assert response.status_code == 422


async def test_enqueue_ingestion_chunk_overlap_not_smaller_than_chunk_size_returns_422(
    client, operator_headers, product
) -> None:
    asset_id = await _upload_text_asset(client, operator_headers, product.id)

    response = await client.post(
        f"/api/v1/assets/{asset_id}/ingest",
        json=_ingest_payload(chunk_size=100, chunk_overlap=100),
        headers=operator_headers,
    )
    assert response.status_code == 422


async def test_get_ingestion_status_before_any_request_returns_404(
    client, operator_headers, product
) -> None:
    asset_id = await _upload_text_asset(client, operator_headers, product.id)

    response = await client.get(f"/api/v1/assets/{asset_id}/ingest", headers=operator_headers)
    assert response.status_code == 404


async def test_get_ingestion_status_returns_the_queued_job(
    client, operator_headers, viewer_headers, product
) -> None:
    asset_id = await _upload_text_asset(client, operator_headers, product.id)
    enqueued = await client.post(
        f"/api/v1/assets/{asset_id}/ingest", json=_ingest_payload(), headers=operator_headers
    )
    assert enqueued.status_code == 202

    # Any active role can read status - not just operator/admin.
    response = await client.get(f"/api/v1/assets/{asset_id}/ingest", headers=viewer_headers)
    assert response.status_code == 200
    assert response.json()["id"] == enqueued.json()["id"]
    assert response.json()["status"] == "queued"
