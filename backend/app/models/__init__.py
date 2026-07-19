"""SQLAlchemy models package.

Every model module is imported here so Base.metadata is fully populated
before Alembic autogenerate (or Base.metadata.create_all in tests) runs.
"""
from app.models.agent_definition import AgentDefinition  # noqa: F401
from app.models.agent_run import AgentRun  # noqa: F401
from app.models.asset import Asset  # noqa: F401
from app.models.base import Base  # noqa: F401
from app.models.document_chunk import DocumentChunk  # noqa: F401
from app.models.job import Job  # noqa: F401
from app.models.llm_request import LLMRequestRecord  # noqa: F401
from app.models.product import Product  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.worker_heartbeat import WorkerHeartbeat  # noqa: F401
from app.models.workflow_definition import WorkflowDefinition  # noqa: F401
from app.models.workflow_run import WorkflowRun  # noqa: F401
from app.models.workflow_step_run import WorkflowStepRun  # noqa: F401
