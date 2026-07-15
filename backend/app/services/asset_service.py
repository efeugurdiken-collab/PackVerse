"""Asset service layer - ties app.storage (backend-agnostic I/O) to the
assets table. app/api/v1/assets.py never touches app.storage or
app.models.Asset directly; everything goes through here.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.asset import Asset
from app.models.enums import AssetStatus, StorageProvider
from app.models.product import Product
from app.services.exceptions import (
    AssetDeletedError,
    AssetNotFoundError,
    AssetStorageOperationFailedError,
    EmptyFileError,
    FileTooLargeError,
    InvalidFilenameError,
    ProductNotFoundError,
    UnsupportedFileTypeError,
)
from app.storage.base import StorageBackend
from app.storage.exceptions import StorageError

MAX_PAGE_SIZE = 100
_MAX_FILENAME_LENGTH = 255
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Best-effort magic-byte signatures for the file types where a check is
# simple and reliable. Types without an entry (SVG/JSON are text formats
# with no fixed binary signature; font/STL detection would need a much
# larger signature table for marginal benefit at this sprint's scope)
# fall back to trusting the declared Content-Type - a documented
# limitation, not an oversight; see README Known Limitations.
_MAGIC_BYTES: dict[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "application/pdf": (b"%PDF-",),
    "application/zip": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
}


def sanitize_filename(original_filename: str) -> str:
    """Produces a filesystem/URL-safe filename from client input.

    Never used to build a path directly by itself - it only becomes the
    trailing segment of a server-generated storage key, see
    build_storage_key - but must still be safe standalone, since it's
    also stored as Asset.filename and can end up in a
    Content-Disposition header on download.
    """
    normalized = unicodedata.normalize("NFKD", original_filename)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    base = PurePosixPath(ascii_only).name  # drops any directory components
    safe = _SAFE_FILENAME_RE.sub("_", base).strip("._")
    return (safe or "file")[:_MAX_FILENAME_LENGTH]


def build_storage_key(*, product_id: uuid.UUID, asset_id: uuid.UUID, safe_filename: str) -> str:
    """products/{product_id}/{asset_id}/{safe_filename}.

    Server-generated only, per the Storage Key Rules: namespaced by
    product, unique per asset UUID (so two uploads can never collide
    regardless of filename), POSIX separators, no raw user input beyond
    the already-sanitized filename.
    """
    return f"products/{product_id}/{asset_id}/{safe_filename}"


def _content_matches_declared_type(content: bytes, declared_content_type: str) -> bool:
    signatures = _MAGIC_BYTES.get(declared_content_type)
    if signatures is None:
        return True
    return any(content.startswith(sig) for sig in signatures)


def _validate_upload(*, filename: str, content_type: str, content: bytes) -> None:
    settings = get_settings()

    if not filename or len(filename) > _MAX_FILENAME_LENGTH or "\x00" in filename:
        raise InvalidFilenameError(filename)

    if len(content) == 0:
        raise EmptyFileError()

    if len(content) > settings.max_upload_size_bytes:
        raise FileTooLargeError(len(content), settings.max_upload_size_bytes)

    if content_type not in settings.allowed_mime_types_list:
        raise UnsupportedFileTypeError(content_type)

    if not _content_matches_declared_type(content, content_type):
        raise UnsupportedFileTypeError(content_type)


async def _get_active_product(db: AsyncSession, product_id: uuid.UUID) -> Product:
    product = await db.get(Product, product_id)
    if product is None:
        raise ProductNotFoundError(product_id)
    return product


async def upload_asset(
    db: AsyncSession,
    storage: StorageBackend,
    *,
    product_id: uuid.UUID,
    asset_type: str,
    original_filename: str,
    content_type: str,
    content: bytes,
    uploaded_by_user_id: uuid.UUID,
) -> Asset:
    """Validates, writes to storage, then persists metadata.

    Storage happens first and the database second on purpose: if the
    database commit fails after a successful storage write, the just-
    written object is deleted so nothing is ever left in storage without
    a corresponding row (the sprint's "roll back storage object if
    database persistence fails" requirement). The reverse ordering would
    risk the opposite failure mode - a committed row pointing at an
    object that was never actually written.
    """
    await _get_active_product(db, product_id)
    _validate_upload(filename=original_filename, content_type=content_type, content=content)

    safe_filename = sanitize_filename(original_filename)
    asset_id = uuid.uuid4()
    storage_key = build_storage_key(
        product_id=product_id, asset_id=asset_id, safe_filename=safe_filename
    )
    checksum = hashlib.sha256(content).hexdigest()
    backend_name = StorageProvider(get_settings().storage_backend)

    try:
        storage_metadata = await storage.store(storage_key, content, content_type=content_type)
    except StorageError as exc:
        raise AssetStorageOperationFailedError("write") from exc

    asset = Asset(
        id=asset_id,
        product_id=product_id,
        asset_type=asset_type,
        filename=safe_filename,
        original_filename=original_filename[:_MAX_FILENAME_LENGTH],
        storage_key=storage_key,
        mime_type=content_type,
        content_type=content_type,
        size_bytes=len(content),
        checksum=checksum,
        etag=storage_metadata.etag,
        storage_backend=backend_name,
        status=AssetStatus.AVAILABLE,
        uploaded_by_user_id=uploaded_by_user_id,
    )
    db.add(asset)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        try:
            await storage.delete(storage_key)
        except StorageError:
            # Best-effort cleanup: the DB commit already failed for its
            # own reason, and we don't want a secondary storage error to
            # mask the original one or leave the request hanging.
            pass
        raise
    await db.refresh(asset)
    return asset


async def get_asset(db: AsyncSession, asset_id: uuid.UUID) -> Asset:
    asset = await db.get(Asset, asset_id)
    if asset is None:
        raise AssetNotFoundError(asset_id)
    if asset.status == AssetStatus.DELETED:
        raise AssetDeletedError(asset_id)
    return asset


async def list_assets_for_product(
    db: AsyncSession, product_id: uuid.UUID, *, limit: int = 20, offset: int = 0
) -> tuple[list[Asset], int]:
    limit = min(max(limit, 1), MAX_PAGE_SIZE)
    offset = max(offset, 0)

    base_query = select(Asset).where(
        Asset.product_id == product_id, Asset.status != AssetStatus.DELETED
    )
    total = await db.scalar(
        select(func.count()).select_from(base_query.subquery())
    )
    result = await db.execute(
        base_query.order_by(Asset.created_at.desc()).limit(limit).offset(offset)
    )
    items = list(result.scalars().all())
    return items, int(total or 0)


async def delete_asset(db: AsyncSession, storage: StorageBackend, asset_id: uuid.UUID) -> None:
    """Idempotent: deleting an already-deleted asset succeeds silently
    (matches the storage backends' own idempotent delete() semantics).

    Deletes the storage object first, then marks the row deleted (soft
    delete - status + deleted_at, audit metadata preserved). If the
    storage delete fails, this raises before touching the database at
    all, so the asset is simply left exactly as it was - recoverable,
    never falsely reported as deleted - per the sprint's explicit
    requirement.
    """
    asset = await db.get(Asset, asset_id)
    if asset is None:
        raise AssetNotFoundError(asset_id)
    if asset.status == AssetStatus.DELETED:
        return  # idempotent - already deleted, nothing to do

    try:
        await storage.delete(asset.storage_key)
    except StorageError as exc:
        raise AssetStorageOperationFailedError("delete") from exc

    asset.status = AssetStatus.DELETED
    asset.deleted_at = datetime.now(timezone.utc)
    db.add(asset)
    await db.commit()


@dataclass(frozen=True)
class DownloadPayload:
    """Exactly one of url/content is set. url means the backend supports
    a direct-fetch link (S3) - the API layer should redirect to it.
    content means the backend has no such concept (local) - the API
    layer streams these bytes back itself. Kept together with the asset
    row so the caller has content_type/filename for response headers
    without a second lookup."""

    asset: Asset
    url: str | None
    content: bytes | None


async def get_download_payload(
    db: AsyncSession, storage: StorageBackend, asset_id: uuid.UUID
) -> DownloadPayload:
    """All storage I/O for a download happens here, not in the API
    layer - app/api/v1/assets.py never imports app.storage.exceptions or
    calls storage.open()/get_download_url() directly."""
    asset = await get_asset(db, asset_id)  # raises AssetNotFoundError / AssetDeletedError

    try:
        url = await storage.get_download_url(asset.storage_key)
    except StorageError as exc:
        # Covers StorageNotFound too: the DB row says AVAILABLE but the
        # object is actually missing from the backend - a real
        # inconsistency, not a client error, so it maps to a 502 at the
        # API layer rather than a 404.
        raise AssetStorageOperationFailedError("read") from exc

    if url is not None:
        return DownloadPayload(asset=asset, url=url, content=None)

    try:
        content = await storage.open(asset.storage_key)
    except StorageError as exc:
        raise AssetStorageOperationFailedError("read") from exc

    return DownloadPayload(asset=asset, url=None, content=content)
