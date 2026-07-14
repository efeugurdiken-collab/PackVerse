"""Product service layer - all database access for products lives here.

Keeping this separate from app/api/v1/products.py means the HTTP layer
stays thin (parse request, call service, shape response) and the
business/data logic is independently testable without spinning up FastAPI.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate
from app.services.exceptions import DuplicateSlugError, ProductNotFoundError

MAX_PAGE_SIZE = 100


async def create_product(db: AsyncSession, data: ProductCreate) -> Product:
    product = Product(
        slug=data.slug,
        title=data.title,
        description=data.description,
        product_type=data.product_type,
        price_cents=data.price_cents,
        currency=data.currency,
        metadata_json=data.metadata_json,
    )
    db.add(product)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # The unique constraint on slug is the only one a client can trigger
        # via this endpoint; anything else re-raises as a genuine 500.
        if "ix_products_slug" in str(exc.orig) or "products_slug" in str(exc.orig):
            raise DuplicateSlugError(data.slug) from exc
        raise
    await db.refresh(product)
    return product


async def get_product(db: AsyncSession, product_id: uuid.UUID) -> Product:
    product = await db.get(Product, product_id)
    if product is None:
        raise ProductNotFoundError(product_id)
    return product


async def list_products(
    db: AsyncSession, *, limit: int = 20, offset: int = 0
) -> tuple[list[Product], int]:
    limit = min(max(limit, 1), MAX_PAGE_SIZE)
    offset = max(offset, 0)

    total = await db.scalar(select(func.count()).select_from(Product))

    result = await db.execute(
        select(Product).order_by(Product.created_at.desc()).limit(limit).offset(offset)
    )
    items = list(result.scalars().all())
    return items, int(total or 0)


async def update_product(
    db: AsyncSession, product_id: uuid.UUID, data: ProductUpdate
) -> Product:
    product = await get_product(db, product_id)

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(product, field, value)

    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product
