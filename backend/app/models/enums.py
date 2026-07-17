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


class LLMRequestStatus(str, Enum):
    """Lifecycle of a single LLM Gateway call (Sprint P5). PENDING is set
    the instant the row is created, before the provider call is made;
    the request flips to SUCCEEDED or FAILED once the gateway call
    resolves - see app/services/llm_service.py."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentRunStatus(str, Enum):
    """Lifecycle of a single AgentDefinition execution (Sprint P6: AI
    Runtime). QUEUED is set the instant the row is created, before
    anything runs. The allowed QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED
    transitions are enforced by app/runtime/models.py's state machine,
    not just documented here - see that module for the exact transition
    table."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowRunStatus(str, Enum):
    """Lifecycle of a single WorkflowDefinition execution (Sprint P7:
    Workflow Orchestration) - the same five states as AgentRunStatus,
    with the same transition shape, enforced by
    app/workflows/models.py's state machine. Deliberately a separate
    enum, not a reuse of AgentRunStatus, so the two lifecycles can evolve
    independently (e.g. cancel semantics already differ - see
    app/workflows/service.py's cancel_run)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStepRunStatus(str, Enum):
    """Lifecycle of a single step within a WorkflowRun (Sprint P7).
    PENDING rows for every step are created up front, at workflow-run
    creation time, before any step executes - see
    app/workflows/service.py's create_workflow_run. SKIPPED is reserved
    for steps that never ran because an earlier step failed; CANCELLED
    is reserved for steps that never ran because the workflow run itself
    was explicitly cancelled - the two are kept distinct rather than
    collapsed into one "didn't run" status, since they have different
    causes worth distinguishing in the API response."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
