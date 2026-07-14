"""SQLAlchemy models package.

Every model module is imported here so Base.metadata is fully populated
before Alembic autogenerate (or Base.metadata.create_all in tests) runs.
"""
from app.models.agent_definition import AgentDefinition  # noqa: F401
from app.models.asset import Asset  # noqa: F401
from app.models.base import Base  # noqa: F401
from app.models.job import Job  # noqa: F401
from app.models.product import Product  # noqa: F401
from app.models.workflow_definition import WorkflowDefinition  # noqa: F401
