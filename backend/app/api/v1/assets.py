"""Asset API endpoints (Sprint P4).

Two URL namespaces share this router: uploads/listing are nested under
their product (POST/GET /products/{product_id}/assets), while detail,
download, and delete address an asset directly (/assets/{asset_id}...) -
matching the sprint spec exactly rather than forcing everything under one
prefix.

Authorization mirrors the Product API: any active role can read
(list/detail/download), only operator/admin can upload or delete.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.database.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.asset import AssetRead
from app.schemas.common import Page
from app.services import asset_service
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
from app.storage.factory import get_storage_backend

router = APIRouter(tags=["assets"])

_can_write = require_roles(UserRole.OPERATOR, UserRole.ADMIN)
_can_read = require_roles(UserRole.VIEWER, UserRole.OPERATOR, UserRole.ADMIN)


@router.post(
    "/products/{product_id}/assets",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_asset(
    product_id: uuid.UUID,
    file: UploadFile = File(...),
    asset_type: str = Form(default="file"),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_backend),
    current_user: User = Depends(_can_write),
) -> AssetRead:
    content = await file.read()
    try:
        asset = await asset_service.upload_asset(
            db,
            storage,
            product_id=product_id,
            asset_type=asset_type,
            original_filename=file.filename or "file",
            content_type=file.content_type or "application/octet-stream",
            content=content,
            uploaded_by_user_id=current_user.id,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (EmptyFileError, InvalidFilenameError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except UnsupportedFileTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc
    except AssetStorageOperationFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="storage operation failed"
        ) from exc
    return AssetRead.model_validate(asset)


@router.get(
    "/products/{product_id}/assets",
    response_model=Page[AssetRead],
    dependencies=[Depends(_can_read)],
)
async def list_assets(
    product_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> Page[AssetRead]:
    items, total = await asset_service.list_assets_for_product(
        db, product_id, limit=limit, offset=offset
    )
    return Page[AssetRead](
        items=[AssetRead.model_validate(a) for a in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/assets/{asset_id}", response_model=AssetRead, dependencies=[Depends(_can_read)])
async def get_asset(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> AssetRead:
    try:
        asset = await asset_service.get_asset(db, asset_id)
    except AssetNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AssetDeletedError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AssetRead.model_validate(asset)


@router.get("/assets/{asset_id}/download", dependencies=[Depends(_can_read)])
async def download_asset(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_backend),
) -> Response:
    try:
        payload = await asset_service.get_download_payload(db, storage, asset_id)
    except AssetNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AssetDeletedError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AssetStorageOperationFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="storage operation failed"
        ) from exc

    if payload.url is not None:
        # S3 backend: a short-lived signed URL. Never proxy the bytes
        # through this process, and never expose the bucket/credentials
        # used to sign it - the client fetches directly from the
        # provider using a URL that expires on its own.
        return RedirectResponse(payload.url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    # Local backend: stream the bytes ourselves. filename here is the
    # server-sanitized name (never original_filename, which is unsanitized
    # client input) - see the Safe Content-Disposition requirement.
    asset = payload.asset
    media_type = asset.content_type or asset.mime_type
    headers = {"Content-Disposition": f'attachment; filename="{asset.filename}"'}
    return Response(content=payload.content, media_type=media_type, headers=headers)


@router.delete(
    "/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_can_write)],
)
async def delete_asset(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_backend),
) -> None:
    try:
        await asset_service.delete_asset(db, storage, asset_id)
    except AssetNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AssetStorageOperationFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="storage operation failed"
        ) from exc
