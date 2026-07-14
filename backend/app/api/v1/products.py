"""Product API endpoints.

Sprint P2 scope: create, read (single + paginated list), and update only.
No delete endpoint and no authentication yet, per the sprint spec.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.schemas.common import Page
from app.schemas.product import ProductCreate, ProductRead, ProductUpdate
from app.services import product_service
from app.services.exceptions import DuplicateSlugError, ProductNotFoundError

router = APIRouter(prefix="/products", tags=["products"])


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate, db: AsyncSession = Depends(get_db)
) -> ProductRead:
    try:
        product = await product_service.create_product(db, payload)
    except DuplicateSlugError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ProductRead.model_validate(product)


@router.get("/{product_id}", response_model=ProductRead)
async def get_product(
    product_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ProductRead:
    try:
        product = await product_service.get_product(db, product_id)
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ProductRead.model_validate(product)


@router.get("", response_model=Page[ProductRead])
async def list_products(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> Page[ProductRead]:
    items, total = await product_service.list_products(db, limit=limit, offset=offset)
    return Page[ProductRead](
        items=[ProductRead.model_validate(p) for p in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/{product_id}", response_model=ProductRead)
async def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdate,
    db: AsyncSession = Depends(get_db),
) -> ProductRead:
    try:
        product = await product_service.update_product(db, product_id, payload)
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ProductRead.model_validate(product)
