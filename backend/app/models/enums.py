"""Database-safe enumerations shared across models and schemas.

Stored as SQLAlchemy Enum(..., native_enum=False) - i.e. VARCHAR + CHECK
constraint rather than a native PostgreSQL ENUM type. This is a deliberate
tradeoff: native Postgres enums are marginally more storage-efficient but
require ALTER TYPE ... ADD VALUE migrations (which cannot run inside a
transaction in older PostgreSQL versions) whenever a new status is added.
A CHECK-constrained VARCHAR is trivial to evolve with a normal migration.
"""
from enum import Enum


class ProductStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class ProductType(str, Enum):
    """Mirrors the product lines defined in the PackVerse OS vault (07 Products/)."""

    BRAND_KIT = "brand_kit"
    PROMPT_PACK = "prompt_pack"
    TEXTURE_PACK = "texture_pack"
    SVG_PACK = "svg_pack"
    MOCKUP_PACK = "mockup_pack"
    PRESET_PACK = "preset_pack"
    TATTOO_PACK = "tattoo_pack"
    STL_PACK = "stl_pack"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class UserRole(str, Enum):
    """Ordered least to most privileged - see app/api/deps.py's
    require_roles() for how this ordering maps to the Product API's
    viewer/operator/admin access matrix."""

    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class UserStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"


class AssetStatus(str, Enum):
    """Lifecycle of a single asset upload (Sprint P4).

    PENDING is set the instant the database row is created, before the
    storage write is attempted; the upload endpoint flips it to
    AVAILABLE only after the storage backend confirms the write
    succeeded, or to FAILED (row kept for auditability, not deleted) if
    it didn't. DELETED is a soft-delete marker - see Asset.deleted_at.
    """

    PENDING = "pending"
    AVAILABLE = "available"
    FAILED = "failed"
    DELETED = "deleted"


class StorageProvider(str, Enum):
    """Which storage backend a given asset's bytes actually live in -
    recorded per-row (not read from current app config) so that assets
    uploaded under one backend stay retrievable by key even after the
    deployment's default STORAGE_BACKEND setting changes later."""

    LOCAL = "local"
    S3 = "s3"
