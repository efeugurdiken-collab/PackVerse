"""FastAPI application entrypoint.

Sprint P1 scope: infrastructure only. No AI features, no Agents, no MCP,
no RAG - those are introduced in later sprints per the PackVerse OS
Obsidian vault roadmap (10 Roadmap/Current Sprint.md).
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_v1_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)
    yield
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)

app.include_router(api_v1_router, prefix=settings.api_v1_prefix)


@app.get("/", tags=["root"])
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "running"}
