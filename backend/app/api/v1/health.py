"""Health check endpoint.

Used by Docker healthchecks, load balancers, and CI to confirm the
service is up and its dependencies (currently: PostgreSQL) are reachable.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.database.session import check_database_connection

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    database: str


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    db_ok = await check_database_connection()
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database="connected" if db_ok else "unreachable",
    )
