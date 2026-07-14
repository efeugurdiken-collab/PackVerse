"""Product model - the catalog entry for a shippable digital product.

Corresponds to the product-specification documents in the PackVerse OS
vault (07 Products/). One row here represents one catalog product;
its individual files are represented by Asset rows.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import ProductStatus, ProductType

if TYPE_CHECKING:
    from app.models.asset import Asset


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="ck_products_price_cents_non_negative"),
        CheckConstraint("char_length(currency) = 3", name="ck_products_currency_iso4217_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_type: Mapped[ProductType] = mapped_column(
        SAEnum(ProductType, name="product_type", native_enum=False, validate_strings=True),
        nullable=False,
    )
    status: Mapped[ProductStatus] = mapped_column(
        SAEnum(ProductStatus, name="product_status", native_enum=False, validate_strings=True),
        nullable=False,
        default=ProductStatus.DRAFT,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1.0")
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)

    assets: Mapped[list[Asset]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Product id={self.id} slug={self.slug!r} status={self.status}>"
