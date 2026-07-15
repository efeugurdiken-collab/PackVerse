"""Asset model - a single delivered file belonging to a Product.

Only file metadata lives here (per the P2 database rules: binary asset
data is never stored in PostgreSQL). The actual bytes live in object
storage - app/storage/ - as of Sprint P4; storage_key is the pointer to
that location, and storage_backend records which backend the key should
be resolved against.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import AssetStatus, StorageProvider

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.user import User


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)

    # --- Sprint P4 ---
    # original_filename is the client-supplied name, preserved only as
    # metadata (never used to build storage_key or a filesystem path -
    # see the Local Storage requirement this satisfies). filename holds
    # the sanitized, storage-safe name actually embedded in storage_key.
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # content_type is the server-detected/verified MIME type used for
    # the actual upload decision; mime_type is kept (unchanged, still
    # NOT NULL) for backward compatibility with P2 rows and is populated
    # with the same value going forward.
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_backend: Mapped[StorageProvider] = mapped_column(
        SAEnum(StorageProvider, name="storage_provider", native_enum=False, validate_strings=True),
        nullable=False,
        default=StorageProvider.LOCAL,
    )
    status: Mapped[AssetStatus] = mapped_column(
        SAEnum(AssetStatus, name="asset_status", native_enum=False, validate_strings=True),
        nullable=False,
        default=AssetStatus.PENDING,
        index=True,
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    product: Mapped[Product] = relationship(back_populates="assets")
    uploaded_by: Mapped[User | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<Asset id={self.id} product_id={self.product_id} "
            f"filename={self.filename!r} status={self.status}>"
        )
