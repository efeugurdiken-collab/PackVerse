"""SQLAlchemy models package.

Import every model module here once models exist, so Base.metadata
is fully populated before Alembic autogenerate runs.
"""
from app.models.base import Base  # noqa: F401
